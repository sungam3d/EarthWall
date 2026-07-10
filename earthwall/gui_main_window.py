from __future__ import annotations

import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap, QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QSizePolicy, QSlider, QSpinBox,
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
PREVIEW_WIDTH, PREVIEW_HEIGHT = 1280, 640

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
        self.setMinimumSize(720, 640)

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

        self.preview_label = QLabel("No preview yet - rendering…", self.preview_container)
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setStyleSheet("background:#111; color:#888; border-radius:6px;")
        self.preview_label.setScaledContents(False)
        self.preview_label.setMinimumSize(1, 1)  # allow shrinking; parent controls actual size
        self.preview_label.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Ignored,
        )
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
        tabs.addTab(self._build_general_tab(), "General")
        tabs.addTab(self._build_map_tab(), "Map && View")
        tabs.addTab(self._build_cities_tab(), "Cities")
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
        layout.addWidget(clouds_box)

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
        if self.resolution_combo.currentIndex() == 0:
            return _detect_resolution()
        return self.width_spin.value(), self.height_spin.value()

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
        self._preview_worker = RenderWorker(
            dict(self.settings), list(self.cities), str(PREVIEW_OUTPUT),
            PREVIEW_WIDTH, PREVIEW_HEIGHT, apply_wallpaper=False,
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
        target_h = min(max_h, available_w // 2)
        self.preview_container.setFixedHeight(target_h)

    def _show_preview_pixmap(self, output_path: str) -> None:
        self._last_preview_pixmap = QPixmap(output_path)
        self._rescale_preview()

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
