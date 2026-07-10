from __future__ import annotations

import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap, QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QSlider, QSpinBox,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from . import autostart, maps as maps_module, settings as settings_module
from .gui_city_dialog import CityDialog
from .gui_map_dialog import ImportMapDialog
from .gui_widgets import ClickJumpSlider
from .gui_worker import RenderWorker
from .wallpaper import pick_next_wallpaper_path

# Base name for the wallpaper output. Actual files alternate between
# current_a.jpg / current_b.jpg (see pick_next_wallpaper_path) so the file
# the desktop is displaying is never overwritten in place. JPEG rather
# than PNG: a 4K PNG is ~10x larger and noticeably slower for the DE to
# decode, which stretches out the visible wallpaper transition.
WALLPAPER_BASE = Path.home() / ".cache" / "earthwall" / "current.jpg"
PREVIEW_OUTPUT = Path.home() / ".cache" / "earthwall" / "preview.jpg"

# Deliberately small - this is what re-renders on every tweak (city added,
# slider dragged, map switched), so it needs to feel instant. The actual
# wallpaper still renders at full resolution via the timer / Update Now.
# NOTE: only the width is fixed - the height is derived from the current
# target resolution's aspect ratio (see _preview_render_size), so slider-
# triggered previews and full "Update Now" renders always have the same
# shape and the preview never stretches between the two.
PREVIEW_WIDTH = 1280

# How long to wait after the last change before actually re-rendering the
# preview - stops a slider drag from queuing up dozens of renders.
PREVIEW_DEBOUNCE_MS = 350


def _detect_resolution() -> tuple[int, int]:
    try:
        out = subprocess.run(["xrandr"], capture_output=True, text=True, check=True).stdout
        for line in out.splitlines():
            if " connected" in line and "primary" in line:
                for token in line.split():
                    if "x" in token and token[0].isdigit():
                        w, h = token.split("+")[0].split("x")
                        return int(w), int(h)
    except Exception:
        pass
    return 3840, 2160


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("EarthWall")
        # Wide enough for the longest form rows, tall enough for preview +
        # the tallest tab without scrolling on a typical 1080p screen.
        self.setMinimumSize(760, 680)
        self.resize(820, 860)

        self.settings = settings_module.load_settings()
        self.cities = settings_module.load_cities()
        self._worker: RenderWorker | None = None
        self._preview_worker: RenderWorker | None = None
        self._last_preview_pixmap: QPixmap | None = None
        self._initializing = True

        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.trigger_update)

        self._preview_debounce = QTimer(self)
        self._preview_debounce.setSingleShot(True)
        self._preview_debounce.timeout.connect(self.trigger_preview_update)

        # Ticks the "Local time" column in the cities table and the
        # next-update countdown - cheap text updates, no rendering.
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._on_clock_tick)
        self._clock_timer.start(1000)

        self._build_ui()
        self._load_settings_into_ui()
        self._refresh_map_list()
        self._refresh_city_table()
        self._restart_timer()
        self._initializing = False

        # Kick off a first render shortly after launch.
        QTimer.singleShot(500, self.trigger_update)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # --- Preview -----------------------------------------------------
        preview_box = QGroupBox("Preview")
        preview_layout = QVBoxLayout(preview_box)

        # The preview container has a FIXED height set from the window
        # width (2:1 aspect ratio matching the equirectangular map), and
        # the QLabel inside is force-scaled to fit. Without this, showing
        # a pixmap of a different aspect ratio (or switching from the
        # "rendering..." text placeholder to an image) causes the QLabel
        # to resize its minimum, which reflows the container and the whole
        # window - the "widening and shrinking" the user was seeing while
        # changing settings.
        self.preview_container = QWidget()
        self.preview_container.setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.Fixed,
        )
        container_layout = QVBoxLayout(self.preview_container)
        container_layout.setContentsMargins(0, 0, 0, 0)

        self.preview_label = QLabel(self.preview_container)
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setStyleSheet(
            "background:#111; color:#bbb; border-radius:6px;"
            " font-size: 13px; padding: 24px;")
        self.preview_label.setWordWrap(True)
        self.preview_label.setScaledContents(False)
        self.preview_label.setMinimumSize(1, 1)  # allow shrinking; parent controls actual size
        self.preview_label.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Ignored,
        )
        # First-load spinner: an animated dot cycle + a note explaining why
        # the first render is slow (fetching the 4MB cloud PNG, resampling
        # ~5400x2700 source maps). Once a pixmap lands the timer stops.
        self._spinner_dots = 0
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(400)
        self._spinner_timer.timeout.connect(self._tick_spinner)
        self._tick_spinner()  # paint initial message
        self._spinner_timer.start()
        container_layout.addWidget(self.preview_label)

        # Parented to the container (not added to a layout) so we can
        # position it manually as an overlay - see _position_progress_bar.
        self.progress = QProgressBar(self.preview_container)
        self.progress.setRange(0, 0)  # indeterminate/"busy" animation
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.setStyleSheet(
            "QProgressBar { background: rgba(0,0,0,0); border: none; }"
            "QProgressBar::chunk { background-color: #4a9eff; border-radius: 2px; }"
        )
        self.progress.hide()
        self.progress.raise_()

        preview_layout.addWidget(self.preview_container)

        preview_btn_row = QHBoxLayout()
        self.status_label = QLabel("Not updated yet")
        self.status_label.setStyleSheet("color:#888;")
        self.next_label = QLabel("")
        self.next_label.setStyleSheet("color:#666;")
        refresh_btn = QPushButton("Update Now")
        refresh_btn.clicked.connect(self.trigger_update)
        self.pause_btn = QPushButton("Pause Auto-Update")
        self.pause_btn.setCheckable(True)
        self.pause_btn.toggled.connect(self._on_pause_toggled)
        preview_btn_row.addWidget(self.status_label, stretch=1)
        preview_btn_row.addWidget(self.next_label)
        preview_btn_row.addWidget(self.pause_btn)
        preview_btn_row.addWidget(refresh_btn)
        preview_layout.addLayout(preview_btn_row)
        root.addWidget(preview_box)

        # --- Tabs ----------------------------------------------------------
        tabs = QTabWidget()
        # Every tab lives inside a scroll area: whatever the platform font/
        # theme does to widget heights, no setting can ever be cut off -
        # worst case a scrollbar appears instead.
        tabs.addTab(self._make_scrollable(self._build_general_tab()), "General")
        tabs.addTab(self._make_scrollable(self._build_map_tab()), "Map && View")
        tabs.addTab(self._make_scrollable(self._build_displays_tab()), "Displays")
        tabs.addTab(self._make_scrollable(self._build_cities_tab()), "Cities")
        root.addWidget(tabs, stretch=1)

    def _build_general_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        form = QFormLayout()
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 180)
        self.interval_spin.setSuffix(" minutes")
        self.interval_spin.valueChanged.connect(self._on_settings_changed)
        form.addRow("Update every:", self.interval_spin)

        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(["Auto-detect", "Custom"])
        self.resolution_combo.currentIndexChanged.connect(self._on_resolution_mode_changed)
        form.addRow("Wallpaper resolution:", self.resolution_combo)

        res_row = QHBoxLayout()
        self.width_spin = QSpinBox()
        self.width_spin.setRange(640, 15360)
        self.width_spin.setValue(3840)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(360, 8640)
        self.height_spin.setValue(2160)
        self.width_spin.valueChanged.connect(self._on_settings_changed)
        self.height_spin.valueChanged.connect(self._on_settings_changed)
        res_row.addWidget(self.width_spin)
        res_row.addWidget(QLabel("x"))
        res_row.addWidget(self.height_spin)
        res_row.addStretch()
        form.addRow("Custom size:", res_row)

        layout.addLayout(form)

        self.autostart_check = QCheckBox("Start automatically when I log in")
        self.autostart_check.toggled.connect(self._on_autostart_toggled)
        layout.addWidget(self.autostart_check)

        clouds_box = QGroupBox("Live clouds")
        clouds_layout = QVBoxLayout(clouds_box)
        self.clouds_check = QCheckBox("Overlay near-real-time cloud cover (updates every ~3 hours, needs internet)")
        self.clouds_check.toggled.connect(self._on_settings_changed)
        clouds_layout.addWidget(self.clouds_check)

        opacity_row = QHBoxLayout()
        opacity_row.addWidget(QLabel("Cloud opacity:"))
        self.cloud_opacity_slider = ClickJumpSlider(Qt.Horizontal)
        self.cloud_opacity_slider.setRange(0, 100)
        self.cloud_opacity_slider.valueChanged.connect(self._on_settings_changed)
        opacity_row.addWidget(self.cloud_opacity_slider)
        clouds_layout.addLayout(opacity_row)

        # Cloud DENSITY thins out the cloud field itself (wispy cloud is
        # culled first, solid cores stay) - distinct from opacity, which
        # fades everything uniformly. 100% = the raw satellite coverage.
        density_row = QHBoxLayout()
        density_row.addWidget(QLabel("Cloud density:"))
        self.cloud_density_slider = ClickJumpSlider(Qt.Horizontal)
        self.cloud_density_slider.setRange(0, 100)
        self.cloud_density_slider.setValue(100)
        self.cloud_density_slider.valueChanged.connect(self._on_settings_changed)
        density_row.addWidget(self.cloud_density_slider)
        self.cloud_density_value_label = QLabel("100%")
        density_row.addWidget(self.cloud_density_value_label)
        clouds_layout.addLayout(density_row)
        layout.addWidget(clouds_box)

        night_box = QGroupBox("Night side")
        night_box_layout = QVBoxLayout(night_box)
        self.night_view_check = QCheckBox(
            "Show night side (city lights + day/night terminator)")
        self.night_view_check.setChecked(True)
        self.night_view_check.toggled.connect(self._on_settings_changed)
        night_box_layout.addWidget(self.night_view_check)
        layout.addWidget(night_box)

        temp_box = QGroupBox("City weather display")
        temp_form = QFormLayout(temp_box)
        self.temp_units_combo = QComboBox()
        self.temp_units_combo.addItem("Celsius (°C)", "C")
        self.temp_units_combo.addItem("Fahrenheit (°F)", "F")
        self.temp_units_combo.currentIndexChanged.connect(self._on_settings_changed)
        temp_form.addRow("Temperature units:", self.temp_units_combo)
        layout.addWidget(temp_box)

        layout.addStretch()
        return w

    @staticmethod
    def _make_scrollable(page: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidget(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        return scroll

    def _build_map_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        layout.addWidget(QLabel("Choose which world map image to render:"))
        self.map_list = QListWidget()
        self.map_list.currentItemChanged.connect(self._on_map_selected)
        layout.addWidget(self.map_list)

        map_btn_row = QHBoxLayout()
        import_btn = QPushButton("Import New Map…")
        import_btn.clicked.connect(self._on_import_map)
        self.delete_map_btn = QPushButton("Delete Selected")
        self.delete_map_btn.clicked.connect(self._on_delete_map)
        map_btn_row.addWidget(import_btn)
        map_btn_row.addWidget(self.delete_map_btn)
        map_btn_row.addStretch()
        layout.addLayout(map_btn_row)

        center_box = QGroupBox("Map center position")
        center_layout = QVBoxLayout(center_box)
        center_layout.addWidget(QLabel(
            "Shift which longitude sits in the middle of the map "
            "(0° = Prime Meridian/Africa-Europe centered, the default)."
        ))
        slider_row = QHBoxLayout()
        self.center_lon_slider = ClickJumpSlider(Qt.Horizontal)
        self.center_lon_slider.setRange(-180, 180)
        self.center_lon_spin = QSpinBox()
        self.center_lon_spin.setRange(-180, 180)
        self.center_lon_spin.setSuffix("°")
        self.center_lon_slider.valueChanged.connect(self.center_lon_spin.setValue)
        self.center_lon_spin.valueChanged.connect(self.center_lon_slider.setValue)
        self.center_lon_spin.valueChanged.connect(self._on_settings_changed)
        slider_row.addWidget(self.center_lon_slider)
        slider_row.addWidget(self.center_lon_spin)
        center_layout.addLayout(slider_row)

        presets_row = QHBoxLayout()
        for label, lon in [("Americas", -90), ("Atlantic (default)", 0),
                            ("Asia", 100), ("Pacific / Australia", 150)]:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _, v=lon: self.center_lon_spin.setValue(v))
            presets_row.addWidget(btn)
        center_layout.addLayout(presets_row)

        layout.addWidget(center_box)

        twilight_box = QGroupBox("Day/night edge softness")
        twilight_layout = QHBoxLayout(twilight_box)
        twilight_layout.addWidget(QLabel("Sharp"))
        self.twilight_slider = ClickJumpSlider(Qt.Horizontal)
        self.twilight_slider.setRange(1, 18)
        self.twilight_slider.setToolTip(
            "Width of the twilight blend along the terminator, in degrees. "
            "Small = crisp line, large = wide soft dusk band."
        )
        self.twilight_slider.valueChanged.connect(self._on_settings_changed)
        twilight_layout.addWidget(self.twilight_slider)
        twilight_layout.addWidget(QLabel("Soft"))
        self.twilight_value_label = QLabel("")
        self.twilight_value_label.setStyleSheet("color:#888;")
        self.twilight_value_label.setMinimumWidth(28)
        twilight_layout.addWidget(self.twilight_value_label)
        layout.addWidget(twilight_box)

        night_box = QGroupBox("Night side darkness")
        night_layout = QHBoxLayout(night_box)
        night_layout.addWidget(QLabel("Show landscape"))
        self.night_darkness_slider = ClickJumpSlider(Qt.Horizontal)
        self.night_darkness_slider.setRange(0, 100)
        self.night_darkness_slider.setToolTip(
            "How dark the unlit night side gets. Higher = deeper black with "
            "just city lights showing; lower = you can still faintly see the "
            "landscape underneath (the old default)."
        )
        self.night_darkness_slider.valueChanged.connect(self._on_settings_changed)
        night_layout.addWidget(self.night_darkness_slider)
        night_layout.addWidget(QLabel("Fully dark"))
        self.night_darkness_value_label = QLabel("")
        self.night_darkness_value_label.setStyleSheet("color:#888;")
        self.night_darkness_value_label.setMinimumWidth(40)
        night_layout.addWidget(self.night_darkness_value_label)
        layout.addWidget(night_box)

        layout.addStretch()
        return w

    def _build_displays_tab(self) -> QWidget:
        """The multi-monitor / display placement editor.

        UI mirrors EarthView's wallpaper editor:
          - Mode selector (Mirror / Span / Independent)
          - List of detected displays with a Refresh button
          - Large Screen Area preview (virtual desktop + monitors + red-
            outlined map area)
          - Small Map Area preview with a draggable red focal-point dot
            that updates the map center longitude/latitude in real time

        Placement/zoom controls store values under the monitor_configs
        schema in settings; the renderer honours them once Phase 2.6
        lands. Until then the app still runs in the legacy "mirror" mode
        (single map stretched to primary monitor) which is what it did
        before, so nothing breaks for existing users."""
        from .gui_display_widgets import ScreenAreaPreview, MapFocalPointPreview
        from .monitors import detect_layout
        from . import maps as maps_module

        w = QWidget()
        outer = QVBoxLayout(w)

        # ----- Mode -----
        mode_box = QGroupBox("Multi-monitor mode")
        mode_form = QFormLayout(mode_box)
        self.monitors_mode_combo = QComboBox()
        # A few Windows 11 users have seen the popup render at only one
        # row when a QComboBox lives inside a QScrollArea (which every
        # tab does now). Setting a generous max visible count + a real
        # minimum contents length forces Qt to size the popup properly
        # for all three items, on every platform theme.
        self.monitors_mode_combo.setMaxVisibleItems(10)
        self.monitors_mode_combo.setMinimumContentsLength(38)
        # Give the combo an ItemView so the popup detaches cleanly from
        # the scroll area's viewport - some styles otherwise inherit the
        # viewport's clip region and hide rows.
        from PySide6.QtWidgets import QListView
        self.monitors_mode_combo.setView(QListView())
        self.monitors_mode_combo.addItem(
            "Mirror  —  same map on every monitor", "mirror")
        self.monitors_mode_combo.addItem(
            "Stretch  —  one map across all monitors", "span")
        self.monitors_mode_combo.addItem(
            "Custom per-monitor  —  each monitor its own view", "independent")
        self.monitors_mode_combo.currentIndexChanged.connect(self._on_settings_changed)
        mode_form.addRow("Mode:", self.monitors_mode_combo)
        self.monitors_mode_note = QLabel(
            "Stretch composes one wide map across all monitors, filling "
            "any gaps (diagonal layouts, zoom < 100%) with the void colour "
            "below. Custom per-monitor lets each monitor have its own "
            "zoom, focal point, and void fill — pick which monitor to "
            "edit from the selector that appears."
        )
        self.monitors_mode_note.setWordWrap(True)
        self.monitors_mode_note.setStyleSheet("color:#888; font-size:11px;")
        mode_form.addRow(self.monitors_mode_note)
        outer.addWidget(mode_box)

        # ----- Detected displays -----
        det_box = QGroupBox("Detected displays")
        det_layout = QVBoxLayout(det_box)
        det_row = QHBoxLayout()
        self.displays_summary_label = QLabel("Detecting…")
        self.displays_summary_label.setWordWrap(True)
        det_row.addWidget(self.displays_summary_label, stretch=1)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_monitor_layout)
        det_row.addWidget(refresh_btn)
        det_layout.addLayout(det_row)
        outer.addWidget(det_box)

        # Per-monitor editor selector - only meaningful in "independent"
        # mode where each monitor can have its own view. In mirror/span
        # the row hides itself (monitor 0's config represents the whole
        # thing) so casual users aren't confronted with an irrelevant
        # dropdown.
        self.monitor_editor_row = QWidget()
        editor_layout = QHBoxLayout(self.monitor_editor_row)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.addWidget(QLabel("Editing monitor:"))
        self.monitor_editor_combo = QComboBox()
        self.monitor_editor_combo.currentIndexChanged.connect(
            self._on_active_monitor_changed)
        editor_layout.addWidget(self.monitor_editor_combo)
        editor_layout.addWidget(QLabel(
            " – each monitor keeps its own zoom, focal point, and void fill."))
        editor_layout.addStretch()
        self.monitor_editor_row.setVisible(False)
        outer.addWidget(self.monitor_editor_row)

        # ----- Screen Area preview -----
        screen_box = QGroupBox("Screen Area (your desktop)")
        screen_layout = QVBoxLayout(screen_box)
        self.screen_area_preview = ScreenAreaPreview()
        self.screen_area_preview.setMinimumHeight(220)
        screen_layout.addWidget(self.screen_area_preview)
        outer.addWidget(screen_box)

        # ----- Map Area (focal point + zoom) -----
        map_area_box = QGroupBox("Map Area (drag the red dot to change center)")
        map_area_layout = QVBoxLayout(map_area_box)

        # Load a base map thumbnail so the focal-picker shows real coastlines
        # instead of a blue rect - makes the dot's target far easier to see.
        map_thumb_path = None
        try:
            sets = maps_module.list_map_sets()
            active = self.settings.get("map_set", "blue_marble_july")
            if active in sets:
                map_thumb_path = str(sets[active]["day_path"])
            elif sets:
                map_thumb_path = str(next(iter(sets.values()))["day_path"])
        except Exception:
            map_thumb_path = None

        self.map_focal_preview = MapFocalPointPreview(map_path=map_thumb_path)
        self.map_focal_preview.focal_changed.connect(self._on_map_focal_changed)
        map_area_layout.addWidget(self.map_focal_preview)

        zoom_row = QHBoxLayout()
        zoom_row.addWidget(QLabel("Zoom:"))
        self.map_zoom_slider = ClickJumpSlider(Qt.Horizontal)
        self.map_zoom_slider.setRange(50, 400)  # 50% (zoomed out) to 400% (zoomed in)
        self.map_zoom_slider.setValue(100)
        self.map_zoom_slider.valueChanged.connect(self._on_settings_changed)
        zoom_row.addWidget(self.map_zoom_slider)
        self.map_zoom_value_label = QLabel("100%")
        self.map_zoom_value_label.setMinimumWidth(40)
        zoom_row.addWidget(self.map_zoom_value_label)
        map_area_layout.addLayout(zoom_row)

        # Void fill: colour picker for the area outside the map when the
        # map doesn't cover the full virtual desktop (zoomed in, or
        # diagonal monitor layout with gaps).
        # Void fill: either a colour swatch (click for QColorDialog) or a
        # background image (click to browse). Image takes precedence over
        # colour when set - the label next to them shows which is active.
        void_row = QHBoxLayout()
        void_row.addWidget(QLabel("Fill empty screen area with:"))
        self.void_fill_btn = QPushButton()
        self.void_fill_btn.setFixedWidth(60)
        self.void_fill_btn.setToolTip("Pick a solid colour")
        self.void_fill_btn.clicked.connect(self._pick_void_fill_color)
        void_row.addWidget(self.void_fill_btn)
        self.void_fill_image_btn = QPushButton("Image…")
        self.void_fill_image_btn.setToolTip(
            "Pick a background image; clears when unset")
        self.void_fill_image_btn.clicked.connect(self._pick_void_fill_image)
        void_row.addWidget(self.void_fill_image_btn)
        self.void_fill_clear_btn = QPushButton("Clear image")
        self.void_fill_clear_btn.clicked.connect(self._clear_void_fill_image)
        void_row.addWidget(self.void_fill_clear_btn)
        self.void_fill_status = QLabel("colour")
        self.void_fill_status.setStyleSheet("color:#888; font-size:11px;")
        void_row.addWidget(self.void_fill_status)
        void_row.addStretch()
        map_area_layout.addLayout(void_row)

        # Position / Size spinboxes - EarthView-style manual placement.
        # These live alongside the zoom slider: zoom sizes the map, X/Y
        # positions it within the virtual desktop. Both are stored on
        # monitor #0's config (mapped to "spanned map" in span mode).
        # Advanced users can pin exact numbers; casual users can ignore
        # them and just drag the focal dot / move the zoom slider.
        placement_row = QHBoxLayout()
        placement_row.addWidget(QLabel("Map position X:"))
        self.map_pos_x_spin = QSpinBox()
        self.map_pos_x_spin.setRange(-30000, 30000)
        self.map_pos_x_spin.setSuffix(" px")
        self.map_pos_x_spin.valueChanged.connect(self._on_settings_changed)
        placement_row.addWidget(self.map_pos_x_spin)
        placement_row.addWidget(QLabel("Y:"))
        self.map_pos_y_spin = QSpinBox()
        self.map_pos_y_spin.setRange(-30000, 30000)
        self.map_pos_y_spin.setSuffix(" px")
        self.map_pos_y_spin.valueChanged.connect(self._on_settings_changed)
        placement_row.addWidget(self.map_pos_y_spin)
        self.map_pos_auto_btn = QPushButton("Auto-center")
        self.map_pos_auto_btn.setToolTip(
            "Reset X/Y so the map is centred on the virtual desktop")
        self.map_pos_auto_btn.clicked.connect(self._auto_center_map_position)
        placement_row.addWidget(self.map_pos_auto_btn)
        placement_row.addStretch()
        map_area_layout.addLayout(placement_row)

        outer.addWidget(map_area_box)
        outer.addStretch()

        # Initial population happens once, after widgets exist - the
        # layout detection needs a live QApplication so this must run
        # after construction. showEvent takes care of it.
        return w

    def _refresh_monitor_layout(self) -> None:
        from .monitors import detect_layout
        layout = detect_layout()
        self._current_layout = layout
        parts = []
        for m in layout.monitors:
            tag = " (primary)" if m.is_primary else ""
            parts.append(f"#{m.index + 1}{tag}: {m.width}×{m.height} at ({m.x}, {m.y})")
        self.displays_summary_label.setText(
            f"{layout.count} display{'s' if layout.count != 1 else ''} detected — "
            f"virtual desktop {layout.virtual_width}×{layout.virtual_height}.\n"
            + "\n".join(parts)
        )
        self.screen_area_preview.set_layout(layout)
        self._update_screen_area_preview()
        self._rebuild_monitor_editor_combo()

    def _update_screen_area_preview(self) -> None:
        """Recompute the red-outlined map-area rect from current settings
        and push it into the preview. Map area = virtual desktop scaled
        by 1/zoom, centred on the focal point (for a first cut - proper
        per-monitor placement lands with Phase 2.6)."""
        layout = getattr(self, "_current_layout", None)
        if layout is None:
            return
        zoom = self.map_zoom_slider.value() / 100.0
        # At zoom=1 the map exactly covers the virtual desktop; at zoom>1
        # it's larger than the desktop (parts spill off, no void); at
        # zoom<1 it's smaller (void appears around it).
        vw, vh = layout.virtual_width, layout.virtual_height
        aw = int(vw / max(0.01, zoom))
        ah = int(vh / max(0.01, zoom))
        # Centre on the focal point: translate so the focal lon/lat lands
        # in the middle of the primary monitor for now (proper world-to-
        # desktop mapping is Phase 2.6).
        pm = layout.primary()
        ax = pm.x + pm.width // 2 - aw // 2
        ay = pm.y + pm.height // 2 - ah // 2
        self.screen_area_preview.set_map_area((ax, ay, aw, ah))
        if self._last_preview_pixmap is not None:
            self.screen_area_preview.set_map_thumbnail(self._last_preview_pixmap)

    def _on_map_focal_changed(self, lon: float, lat: float) -> None:
        """Draggable red dot moved. In mirror/span the new focal updates
        the global center_lon/center_lat (single map). In independent
        mode it updates the currently-edited monitor's per-monitor
        focal, leaving other monitors untouched."""
        if self._initializing:
            return
        mode = self.settings.get("monitors_mode", "mirror")
        if mode == "independent":
            from .monitors import monitor_config_for, set_monitor_config
            cfg = monitor_config_for(self.settings, self._active_monitor_index())
            cfg["center_lon"] = float(lon)
            cfg["center_lat"] = float(lat)
            set_monitor_config(self.settings, self._active_monitor_index(), cfg)
        else:
            # Mirror/span: mirror into the global fields AND the visible
            # Map & View tab spinbox/slider so the two UIs stay in sync.
            self.center_lon_spin.blockSignals(True)
            self.center_lon_spin.setValue(int(round(lon)))
            self.center_lon_slider.setValue(int(round(lon)))
            self.center_lon_spin.blockSignals(False)
            self.settings["center_lon"] = float(lon)
            self.settings["center_lat"] = float(lat)
        settings_module.save_settings(self.settings)
        self._schedule_preview_update()

    def _pick_void_fill_color(self) -> None:
        from PySide6.QtWidgets import QColorDialog
        current = QColor(self.settings.get("monitor_configs", {})
                         .get(str(self._active_monitor_index()), {}).get("void_fill_color", "#000000"))
        chosen = QColorDialog.getColor(current, self, "Void fill colour")
        if chosen.isValid():
            from .monitors import monitor_config_for, set_monitor_config
            cfg = monitor_config_for(self.settings, self._active_monitor_index())
            cfg["void_fill_color"] = chosen.name()
            set_monitor_config(self.settings, self._active_monitor_index(), cfg)
            settings_module.save_settings(self.settings)
            self._refresh_void_fill_swatch()
            self._schedule_preview_update()

    def _pick_void_fill_image(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        from .monitors import monitor_config_for, set_monitor_config
        cfg = monitor_config_for(self.settings, self._active_monitor_index())
        start_dir = cfg.get("void_fill_image") or ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Pick a background image", start_dir,
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)")
        if path:
            cfg["void_fill_image"] = path
            set_monitor_config(self.settings, self._active_monitor_index(), cfg)
            settings_module.save_settings(self.settings)
            self._refresh_void_fill_swatch()
            self._schedule_preview_update()

    def _clear_void_fill_image(self) -> None:
        from .monitors import monitor_config_for, set_monitor_config
        cfg = monitor_config_for(self.settings, self._active_monitor_index())
        if cfg.get("void_fill_image"):
            cfg["void_fill_image"] = None
            set_monitor_config(self.settings, self._active_monitor_index(), cfg)
            settings_module.save_settings(self.settings)
            self._refresh_void_fill_swatch()
            self._schedule_preview_update()

    def _active_monitor_index(self) -> int:
        """Which monitor's per-monitor config the Displays-tab controls
        are currently editing. Only meaningful in "independent" mode -
        elsewhere everything routes through monitor 0's config, which
        acts as the "global" config for span mode too."""
        mode = self.settings.get("monitors_mode", "mirror")
        if mode == "independent" and hasattr(self, "monitor_editor_combo"):
            idx = self.monitor_editor_combo.currentData()
            if idx is not None:
                return int(idx)
        return 0

    def _rebuild_monitor_editor_combo(self) -> None:
        """Populate the 'Editing monitor' dropdown from the detected
        layout. Called after a Refresh or on first show."""
        layout = getattr(self, "_current_layout", None)
        if layout is None or not hasattr(self, "monitor_editor_combo"):
            return
        self.monitor_editor_combo.blockSignals(True)
        self.monitor_editor_combo.clear()
        for m in layout.monitors:
            tag = " (primary)" if m.is_primary else ""
            self.monitor_editor_combo.addItem(
                f"Monitor #{m.index + 1}{tag} – {m.width}×{m.height}", m.index)
        self.monitor_editor_combo.blockSignals(False)
        mode = self.settings.get("monitors_mode", "mirror")
        self.monitor_editor_row.setVisible(mode == "independent")

    def _on_active_monitor_changed(self, _idx: int) -> None:
        """User picked a different monitor to edit - reload the Displays
        tab controls from that monitor's config so they show its current
        zoom/focal/position/void, not the previous monitor's values."""
        if getattr(self, "_initializing", False):
            return
        from .monitors import monitor_config_for
        cfg = monitor_config_for(self.settings, self._active_monitor_index())
        # Reload without emitting settings-changed - switching which
        # monitor is being edited isn't itself an edit.
        self._initializing = True
        try:
            self.map_zoom_slider.setValue(int(cfg.get("zoom", 1.0) * 100))
            self.map_pos_x_spin.setValue(int(cfg.get("map_pos_x", 0)))
            self.map_pos_y_spin.setValue(int(cfg.get("map_pos_y", 0)))
            self.map_focal_preview.set_focal(
                cfg.get("center_lon", self.settings.get("center_lon", 0.0)),
                cfg.get("center_lat", self.settings.get("center_lat", 0.0)),
            )
            self._refresh_void_fill_swatch()
        finally:
            self._initializing = False

    def _refresh_void_fill_swatch(self) -> None:
        from .monitors import monitor_config_for
        cfg = monitor_config_for(self.settings, self._active_monitor_index())
        color = cfg["void_fill_color"]
        self.void_fill_btn.setStyleSheet(
            f"background-color: {color}; border: 1px solid #666;")
        self.void_fill_btn.setText("")
        # Show the user which void fill is active so it's obvious why
        # clicking the colour swatch doesn't change what they see when an
        # image is set (image takes precedence in the renderer).
        img = cfg.get("void_fill_image")
        if hasattr(self, "void_fill_status"):
            if img:
                from pathlib import Path
                self.void_fill_status.setText(
                    f"using image: {Path(img).name}")
            else:
                self.void_fill_status.setText("using colour")
        if hasattr(self, "void_fill_clear_btn"):
            self.void_fill_clear_btn.setEnabled(bool(img))

    def _auto_center_map_position(self) -> None:
        """Reset map position spinboxes so the map is centred on the
        virtual desktop for the current zoom - the sensible default."""
        layout = getattr(self, "_current_layout", None)
        if layout is None:
            return
        zoom = self.map_zoom_slider.value() / 100.0
        map_w = int(round(layout.virtual_width * zoom))
        map_h = int(round(layout.virtual_height * zoom))
        cx = (layout.virtual_width - map_w) // 2
        cy = (layout.virtual_height - map_h) // 2
        # blockSignals so we don't fire two settings-changed events - the
        # final _on_settings_changed call after both spinboxes update
        # runs once via the normal signal, keeping things debounced.
        self.map_pos_x_spin.blockSignals(True)
        self.map_pos_x_spin.setValue(cx)
        self.map_pos_x_spin.blockSignals(False)
        self.map_pos_y_spin.setValue(cy)  # this one fires the signal

    def _build_cities_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        layout.addWidget(QLabel(
            "Cities shown as markers on the map, each with their current local time."
        ))

        self.city_table = QTableWidget(0, 6)
        self.city_table.setHorizontalHeaderLabels(
            ["Name", "Local time", "Weather", "Timezone", "Coordinates", "Colour"])
        self.city_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.city_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.city_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.city_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.city_table.itemDoubleClicked.connect(lambda _: self._on_edit_city())
        layout.addWidget(self.city_table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add City…")
        add_btn.clicked.connect(self._on_add_city)
        edit_btn = QPushButton("Edit Selected…")
        edit_btn.clicked.connect(self._on_edit_city)
        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(self._on_remove_city)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(edit_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        return w

    # ------------------------------------------------------ settings <-> UI
    def _load_settings_into_ui(self) -> None:
        s = self.settings
        self.interval_spin.blockSignals(True)
        self.interval_spin.setValue(max(1, s["interval_seconds"] // 60))
        self.interval_spin.blockSignals(False)

        if s["resolution"] == "auto":
            self.resolution_combo.setCurrentIndex(0)
        else:
            self.resolution_combo.setCurrentIndex(1)
            self.width_spin.setValue(s["resolution"][0])
            self.height_spin.setValue(s["resolution"][1])
        self._on_resolution_mode_changed()

        self.autostart_check.blockSignals(True)
        self.autostart_check.setChecked(autostart.is_enabled())
        self.autostart_check.blockSignals(False)

        self.clouds_check.blockSignals(True)
        self.clouds_check.setChecked(s["live_clouds"])
        self.clouds_check.blockSignals(False)
        self.cloud_opacity_slider.blockSignals(True)
        self.cloud_opacity_slider.setValue(int(s["cloud_opacity"] * 100))
        self.cloud_opacity_slider.blockSignals(False)
        self.cloud_density_slider.blockSignals(True)
        self.cloud_density_slider.setValue(int(s.get("cloud_density", 1.0) * 100))
        self.cloud_density_slider.blockSignals(False)
        self.cloud_density_value_label.setText(f"{self.cloud_density_slider.value()}%")
        self.night_view_check.blockSignals(True)
        self.night_view_check.setChecked(bool(s.get("night_view", True)))
        self.night_view_check.blockSignals(False)

        # --- Multi-monitor / Displays tab ---
        if hasattr(self, "monitors_mode_combo"):
            self.monitors_mode_combo.blockSignals(True)
            mode = s.get("monitors_mode", "mirror")
            for i in range(self.monitors_mode_combo.count()):
                if self.monitors_mode_combo.itemData(i) == mode:
                    self.monitors_mode_combo.setCurrentIndex(i); break
            self.monitors_mode_combo.blockSignals(False)
        if hasattr(self, "map_zoom_slider"):
            from .monitors import monitor_config_for
            cfg = monitor_config_for(s, 0)
            self.map_zoom_slider.blockSignals(True)
            self.map_zoom_slider.setValue(int(cfg["zoom"] * 100))
            self.map_zoom_slider.blockSignals(False)
            self.map_zoom_value_label.setText(f"{self.map_zoom_slider.value()}%")
            if hasattr(self, "map_pos_x_spin"):
                self.map_pos_x_spin.blockSignals(True)
                self.map_pos_x_spin.setValue(int(cfg.get("map_pos_x", 0)))
                self.map_pos_x_spin.blockSignals(False)
                self.map_pos_y_spin.blockSignals(True)
                self.map_pos_y_spin.setValue(int(cfg.get("map_pos_y", 0)))
                self.map_pos_y_spin.blockSignals(False)
            self._refresh_void_fill_swatch()
        if hasattr(self, "map_focal_preview"):
            self.map_focal_preview.set_focal(
                s.get("center_lon", 0.0), s.get("center_lat", 0.0))

        self.center_lon_spin.blockSignals(True)
        self.center_lon_spin.setValue(int(s["center_lon"]))
        self.center_lon_slider.setValue(int(s["center_lon"]))
        self.center_lon_spin.blockSignals(False)

        self.twilight_slider.blockSignals(True)
        self.twilight_slider.setValue(int(s.get("twilight_width_deg", 7)))
        self.twilight_slider.blockSignals(False)
        self.twilight_value_label.setText(f"{self.twilight_slider.value()}°")

        self.night_darkness_slider.blockSignals(True)
        self.night_darkness_slider.setValue(int(s.get("night_darkness", 0.85) * 100))
        self.night_darkness_slider.blockSignals(False)
        self.night_darkness_value_label.setText(f"{self.night_darkness_slider.value()}%")

        self.temp_units_combo.blockSignals(True)
        units = s.get("temp_units", "C")
        for i in range(self.temp_units_combo.count()):
            if self.temp_units_combo.itemData(i) == units:
                self.temp_units_combo.setCurrentIndex(i); break
        self.temp_units_combo.blockSignals(False)

        self.pause_btn.blockSignals(True)
        self.pause_btn.setChecked(s["paused"])
        self.pause_btn.setText("Resume Auto-Update" if s["paused"] else "Pause Auto-Update")
        self.pause_btn.blockSignals(False)

    def _current_resolution(self) -> tuple[int, int]:
        # In multi-monitor "span"/"independent" modes the render target
        # IS the virtual desktop, not any single monitor. This makes both
        # the preview and the applied wallpaper come out at the right
        # shape without the user having to fiddle with the resolution
        # spinboxes to match their virtual-desktop dimensions manually.
        mode = self.settings.get("monitors_mode", "mirror") if hasattr(self, "settings") else "mirror"
        layout = getattr(self, "_current_layout", None)
        if mode in ("span", "independent") and layout is not None and layout.virtual_width > 0:
            return layout.virtual_width, layout.virtual_height
        if self.resolution_combo.currentIndex() == 0:
            return _detect_resolution()
        return self.width_spin.value(), self.height_spin.value()

    def _render_layout(self):
        """The MonitorLayout the render workers should honour, or None
        for pure mirror mode (skips the whole multi-monitor code path
        and keeps the classic single-image render fast for users on one
        monitor with mirror mode)."""
        mode = self.settings.get("monitors_mode", "mirror")
        if mode == "mirror":
            return None
        return getattr(self, "_current_layout", None)

    def _on_settings_changed(self, *_args) -> None:
        if self._initializing:
            return
        self.settings["interval_seconds"] = self.interval_spin.value() * 60
        if self.resolution_combo.currentIndex() == 0:
            self.settings["resolution"] = "auto"
        else:
            self.settings["resolution"] = [self.width_spin.value(), self.height_spin.value()]
        self.settings["live_clouds"] = self.clouds_check.isChecked()
        self.settings["cloud_opacity"] = self.cloud_opacity_slider.value() / 100
        self.settings["cloud_density"] = self.cloud_density_slider.value() / 100
        self.cloud_density_value_label.setText(f"{self.cloud_density_slider.value()}%")
        self.settings["night_view"] = self.night_view_check.isChecked()
        # --- Multi-monitor / Displays tab ---
        if hasattr(self, "monitors_mode_combo"):
            new_mode = self.monitors_mode_combo.currentData() or "mirror"
            mode_changed = self.settings.get("monitors_mode") != new_mode
            self.settings["monitors_mode"] = new_mode
            # Show/hide the per-monitor editor selector as the mode
            # changes (only useful when each monitor can differ).
            if hasattr(self, "monitor_editor_row"):
                self.monitor_editor_row.setVisible(new_mode == "independent")
            # Switching INTO independent for the first time: reload the
            # UI controls from monitor 0's config so they reflect its
            # settings rather than whatever the last edit left.
            if mode_changed and new_mode == "independent":
                self._on_active_monitor_changed(0)
        if hasattr(self, "map_zoom_slider"):
            zoom_pct = self.map_zoom_slider.value()
            self.map_zoom_value_label.setText(f"{zoom_pct}%")
            # Zoom lives in the currently-edited monitor's config: in
            # independent mode each monitor has its own; in mirror/span
            # monitor 0's config is used for the whole thing.
            from .monitors import monitor_config_for, set_monitor_config
            cfg = monitor_config_for(self.settings, self._active_monitor_index())
            cfg["zoom"] = zoom_pct / 100.0
            # Persist the position spinboxes alongside zoom - they're
            # part of the same "how does the map sit on the desktop"
            # concept and the renderer uses them together.
            if hasattr(self, "map_pos_x_spin"):
                cfg["map_pos_x"] = int(self.map_pos_x_spin.value())
                cfg["map_pos_y"] = int(self.map_pos_y_spin.value())
            set_monitor_config(self.settings, self._active_monitor_index(), cfg)
            self._update_screen_area_preview()
        self.settings["center_lon"] = float(self.center_lon_spin.value())
        self.settings["twilight_width_deg"] = float(self.twilight_slider.value())
        self.twilight_value_label.setText(f"{self.twilight_slider.value()}°")
        self.settings["night_darkness"] = self.night_darkness_slider.value() / 100
        self.night_darkness_value_label.setText(f"{self.night_darkness_slider.value()}%")
        self.settings["temp_units"] = self.temp_units_combo.currentData() or "C"
        settings_module.save_settings(self.settings)
        self._restart_timer()
        self._schedule_preview_update()

    def _on_resolution_mode_changed(self) -> None:
        custom = self.resolution_combo.currentIndex() == 1
        self.width_spin.setEnabled(custom)
        self.height_spin.setEnabled(custom)
        self._on_settings_changed()

    def _on_autostart_toggled(self, checked: bool) -> None:
        if checked:
            autostart.enable()
        else:
            autostart.disable()
        self.settings["autostart"] = checked
        settings_module.save_settings(self.settings)

    def _on_pause_toggled(self, checked: bool) -> None:
        self.settings["paused"] = checked
        settings_module.save_settings(self.settings)
        self.pause_btn.setText("Resume Auto-Update" if checked else "Pause Auto-Update")
        self._restart_timer()

    def _restart_timer(self) -> None:
        self.update_timer.stop()
        if not self.settings.get("paused"):
            self.update_timer.start(self.settings["interval_seconds"] * 1000)

    # ----------------------------------------------------------------- maps
    def _refresh_map_list(self) -> None:
        self.map_list.blockSignals(True)
        self.map_list.clear()
        maps = maps_module.list_map_sets()
        selected_row = 0
        for i, (map_id, info) in enumerate(maps.items()):
            label = info["name"] + ("" if info["builtin"] else "  (custom)")
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, map_id)
            self.map_list.addItem(item)
            if map_id == self.settings.get("map_set"):
                selected_row = i
        if self.map_list.count():
            self.map_list.setCurrentRow(selected_row)
        self.map_list.blockSignals(False)

    def _on_map_selected(self, current: QListWidgetItem, _prev) -> None:
        if current is None:
            return
        map_id = current.data(Qt.UserRole)
        self.settings["map_set"] = map_id
        settings_module.save_settings(self.settings)
        info = maps_module.list_map_sets().get(map_id, {})
        self.delete_map_btn.setEnabled(not info.get("builtin", True))
        self._schedule_preview_update()

    def _on_import_map(self) -> None:
        dialog = ImportMapDialog(self)
        if dialog.exec():
            name, day_path, night_path = dialog.result_values()
            try:
                map_id = maps_module.import_map_set(name, day_path, night_path)
            except maps_module.MapImportError as e:
                QMessageBox.warning(self, "Import failed", str(e))
                return
            self._refresh_map_list()
            self.settings["map_set"] = map_id
            settings_module.save_settings(self.settings)
            self._schedule_preview_update()

    def _on_delete_map(self) -> None:
        item = self.map_list.currentItem()
        if not item:
            return
        map_id = item.data(Qt.UserRole)
        confirm = QMessageBox.question(self, "Delete map", "Remove this imported map?")
        if confirm != QMessageBox.Yes:
            return
        try:
            maps_module.delete_map_set(map_id)
        except maps_module.MapImportError as e:
            QMessageBox.warning(self, "Couldn't delete", str(e))
            return
        self._refresh_map_list()
        self._schedule_preview_update()

    # --------------------------------------------------------------- cities
    @staticmethod
    def _city_local_time(city: dict) -> str:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        try:
            return datetime.now(ZoneInfo(city["tz"])).strftime("%H:%M")
        except Exception:
            return "--:--"

    def _city_weather_status(self, city: dict) -> str:
        """Human-readable status for the Weather column of the cities table.
        Distinguishes 'not enabled', 'loading', 'unavailable', and shows the
        actual reading when we have one - so 'no weather appearing' isn't
        indistinguishable from 'weather disabled'."""
        if not city.get("show_weather"):
            return "—"
        from . import weather as weather_module
        reading = weather_module.get_cached(city["lat"], city["lon"])
        if reading is None:
            return "loading…"
        units = self.settings.get("temp_units", "C")
        emoji = reading.emoji or ""
        return f"{emoji} {reading.temp_display(units)}".strip()

    def _refresh_city_table(self) -> None:
        self.city_table.setRowCount(len(self.cities))
        for row, city in enumerate(self.cities):
            self.city_table.setItem(row, 0, QTableWidgetItem(city["name"]))
            self.city_table.setItem(row, 1, QTableWidgetItem(self._city_local_time(city)))
            self.city_table.setItem(row, 2, QTableWidgetItem(self._city_weather_status(city)))
            self.city_table.setItem(row, 3, QTableWidgetItem(city["tz"]))
            coord_text = f"{city['lat']:.2f}, {city['lon']:.2f}"
            self.city_table.setItem(row, 4, QTableWidgetItem(coord_text))
            color_item = QTableWidgetItem("")
            color = city.get("color", [255, 210, 60])
            color_item.setBackground(QColor(*color))
            self.city_table.setItem(row, 5, color_item)

    def _on_clock_tick(self) -> None:
        """Once a second: refresh the countdown label, and (only while the
        window is actually visible) the live local-time column."""
        if self.settings.get("paused"):
            self.next_label.setText("auto-update paused")
        elif self.update_timer.isActive():
            remaining = max(0, self.update_timer.remainingTime())
            mins, secs = divmod(remaining // 1000, 60)
            self.next_label.setText(f"next update in {mins}:{secs:02d}")

        if not self.isVisible():
            return
        for row, city in enumerate(self.cities):
            if row >= self.city_table.rowCount():
                break
            time_item = self.city_table.item(row, 1)
            if time_item is not None:
                new_text = self._city_local_time(city)
                if time_item.text() != new_text:
                    time_item.setText(new_text)
            weather_item = self.city_table.item(row, 2)
            if weather_item is not None:
                new_wtext = self._city_weather_status(city)
                if weather_item.text() != new_wtext:
                    weather_item.setText(new_wtext)

    def _on_add_city(self) -> None:
        dialog = CityDialog(self)
        if dialog.exec():
            self.cities.append(dialog.result_city())
            settings_module.save_cities(self.cities)
            self._refresh_city_table()
            self._schedule_preview_update()

    def _on_edit_city(self) -> None:
        row = self.city_table.currentRow()
        if row < 0:
            return
        dialog = CityDialog(self, existing=self.cities[row])
        if dialog.exec():
            self.cities[row] = dialog.result_city()
            settings_module.save_cities(self.cities)
            self._refresh_city_table()
            self._schedule_preview_update()

    def _on_remove_city(self) -> None:
        row = self.city_table.currentRow()
        if row < 0:
            return
        del self.cities[row]
        settings_module.save_cities(self.cities)
        self._refresh_city_table()
        self._schedule_preview_update()

    # -------------------------------------------------------------- render
    def _schedule_preview_update(self) -> None:
        """(Re)start the debounce timer - rapid-fire changes (like dragging
        the center-longitude slider) collapse into a single render once
        things settle, instead of queuing up a render per tick."""
        # Resolution may have changed - keep the container's shape in sync
        # with the target aspect ratio before the next render lands.
        self._update_preview_size()
        self._rescale_preview()
        self._position_progress_bar()
        self._preview_debounce.start(PREVIEW_DEBOUNCE_MS)

    def _update_busy_indicator(self) -> None:
        busy = (self._worker is not None and self._worker.isRunning()) or \
               (self._preview_worker is not None and self._preview_worker.isRunning())
        if busy:
            self._position_progress_bar()
            self.progress.raise_()
            self.progress.show()
        else:
            self.progress.hide()

    def trigger_update(self, *_qt_args) -> None:
        """Full-resolution render + apply as the desktop wallpaper. Used by
        the auto-update timer, the 'Update Now' button, and the tray.

        Note the *_qt_args sink: Qt's clicked/triggered signals pass a
        'checked' bool as the first positional argument. An earlier version
        took `apply_wallpaper` as the first parameter, so that stray bool
        silently disabled applying the wallpaper whenever the button or
        tray action was used - the timer worked, the button didn't."""
        if self._worker is not None and self._worker.isRunning():
            return  # a render is already in flight, skip this tick
        width, height = self._current_resolution()
        output_path = pick_next_wallpaper_path(WALLPAPER_BASE)
        self.status_label.setText("Rendering…")
        self._worker = RenderWorker(
            dict(self.settings), list(self.cities), str(output_path),
            width, height, apply_wallpaper=True,
            monitor_layout=self._render_layout(),
        )
        self._worker.finished_ok.connect(self._on_render_done)
        self._worker.finished_err.connect(self._on_render_error)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.finished.connect(self._clear_worker_ref)
        self._worker.start()
        self._update_busy_indicator()

    def trigger_preview_update(self) -> None:
        """Fast, low-resolution render for instant visual feedback while
        editing settings - never touches the actual desktop wallpaper."""
        if self._preview_worker is not None and self._preview_worker.isRunning():
            # A preview render is already running; the debounce timer will
            # fire again shortly after it finishes if more changes came in.
            self._preview_debounce.start(PREVIEW_DEBOUNCE_MS)
            return
        pw, ph = self._preview_render_size()
        self._preview_worker = RenderWorker(
            dict(self.settings), list(self.cities), str(PREVIEW_OUTPUT),
            pw, ph, apply_wallpaper=False,
            monitor_layout=self._render_layout(),
        )
        self._preview_worker.finished_ok.connect(self._on_preview_render_done)
        self._preview_worker.finished_err.connect(self._on_render_error)
        self._preview_worker.finished.connect(self._preview_worker.deleteLater)
        self._preview_worker.finished.connect(self._clear_preview_worker_ref)
        self._preview_worker.start()
        self._update_busy_indicator()

    def _clear_worker_ref(self) -> None:
        self._worker = None
        self._update_busy_indicator()

    def _clear_preview_worker_ref(self) -> None:
        self._preview_worker = None
        self._update_busy_indicator()

    def shutdown(self) -> None:
        """Called on application quit - block briefly for any in-flight
        render so we don't tear down the process mid-thread."""
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(5000)
        if self._preview_worker is not None and self._preview_worker.isRunning():
            self._preview_worker.wait(5000)

    def _preview_render_size(self) -> tuple[int, int]:
        """Preview render dimensions: fixed small width, height matching the
        aspect ratio of the CURRENT target resolution. Keeping the preview
        the same shape as the real wallpaper is what prevents the preview
        from appearing stretched after a slider tweak vs. after Update Now
        (which renders at the real resolution and is shown in the same
        label)."""
        w, h = self._current_resolution()
        w = max(1, w)
        return PREVIEW_WIDTH, max(1, round(PREVIEW_WIDTH * h / w))

    def _update_preview_size(self) -> None:
        """Set a fixed container height derived from the window width, so
        the preview always keeps a 2:1 aspect ratio (matching the map
        itself) and never changes size in response to what's drawn into
        it. Called on resize and once at startup."""
        # Available width minus the group box border/padding overhead.
        available_w = max(320, self.centralWidget().width() - 40)
        # Cap the preview height so it doesn't dominate the window - the
        # tabs below it need vertical space too.
        max_h = max(180, min(360, self.height() // 3))
        rw, rh = self._current_resolution()
        aspect = rh / max(1, rw)  # follow the real wallpaper's shape
        target_h = min(max_h, int(available_w * aspect))
        self.preview_container.setFixedHeight(max(120, target_h))

    def _tick_spinner(self) -> None:
        """Animate the first-load placeholder. Runs until the first preview
        pixmap is set, then stops for good - we only ever want this seen
        on the initial slow render."""
        self._spinner_dots = (self._spinner_dots + 1) % 4
        dots = "." * self._spinner_dots + " " * (3 - self._spinner_dots)
        self.preview_label.setText(
            f"Preparing your first Earth view {dots}\n\n"
            "The first render is the slow one:\n"
            " • downloading the live cloud map (~4 MB, cached for 3 hours)\n"
            " • resampling the high-resolution source map to your screen\n"
            " • fetching weather for any cities you've added\n\n"
            "After this it stays cached and updates are near-instant."
        )

    def _stop_spinner(self) -> None:
        if self._spinner_timer.isActive():
            self._spinner_timer.stop()

    def _show_preview_pixmap(self, output_path: str) -> None:
        self._stop_spinner()
        self._last_preview_pixmap = QPixmap(output_path)
        self._rescale_preview()
        # Also feed the miniature into the Displays-tab screen-area
        # preview so it shows the current render inside the map-area
        # rectangle instead of an empty red outline.
        if hasattr(self, "screen_area_preview"):
            self.screen_area_preview.set_map_thumbnail(self._last_preview_pixmap)

    def _rescale_preview(self) -> None:
        if self._last_preview_pixmap is None or self._last_preview_pixmap.isNull():
            return
        # Scale to the container's CURRENT geometry, not the label's - the
        # label's size trails the container by one layout pass, and using
        # a stale label size makes the pixmap smaller than the container
        # and the container's dark background bleeds through around it.
        w = max(320, self.preview_container.width())
        h = max(180, self.preview_container.height())
        scaled = self._last_preview_pixmap.scaled(
            w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview_label.setPixmap(scaled)

    def _position_progress_bar(self) -> None:
        """Manually place the progress bar as an overlay along the bottom
        of the preview - since it's not in a layout, it stays put and
        toggling its visibility can't cause the surrounding layout to
        reflow."""
        w = self.preview_container.width()
        h = self.preview_container.height()
        pad = 8
        bar_h = self.progress.height()
        self.progress.setGeometry(pad, h - bar_h - pad, w - pad * 2, bar_h)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_preview_size()
        self._rescale_preview()
        self._position_progress_bar()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # First show is when the widget geometry becomes real - initialise
        # the fixed preview height then so it doesn't briefly show at zero.
        self._update_preview_size()
        self._rescale_preview()
        self._position_progress_bar()
        # Monitor detection has to happen post-show: QApplication.screens()
        # only returns useful geometry once the app is actually on screen.
        # Guarded so we don't re-detect on every show/hide cycle.
        if not getattr(self, "_monitors_detected_once", False):
            self._monitors_detected_once = True
            self._refresh_monitor_layout()

    def _on_render_done(self, output_path: str) -> None:
        from datetime import datetime
        self._show_preview_pixmap(output_path)
        self.status_label.setText(
            f"Wallpaper updated {datetime.now().strftime('%H:%M:%S')}")

    def _on_preview_render_done(self, output_path: str) -> None:
        self._show_preview_pixmap(output_path)

    def _on_render_error(self, message: str) -> None:
        self.status_label.setText(f"Error: {message}")

    def closeEvent(self, event) -> None:
        event.ignore()
        self.hide()
