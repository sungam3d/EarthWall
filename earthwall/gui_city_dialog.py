from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QCompleter, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget,
)

from .city_database import CITY_DATABASE, search_cities, city_label, find_city
from . import fonts as fonts_module
from .gui_widgets import ClickJumpSlider

try:
    from zoneinfo import available_timezones
    ALL_TIMEZONES = sorted(available_timezones())
except Exception:
    ALL_TIMEZONES = []


class _FieldStyleControls:
    """Reusable widget cluster for one field's font family/style/size/colour.
    Kept as a helper class so name/time/weather/notes can each get the same
    controls without three hundred lines of duplication."""

    def __init__(self, parent: QWidget, field_key: str, defaults: dict,
                 existing: dict, on_change=None):
        self.field_key = field_key
        self._on_change = on_change
        self._color = QColor(*existing.get(f"{field_key}_color",
                                           defaults.get("color", [255, 255, 255])))

        self.family_combo = QComboBox()
        self.family_combo.addItems(fonts_module.list_families())
        family = existing.get(f"{field_key}_font_family",
                               defaults.get("family", fonts_module.DEFAULT_FAMILY))
        idx = self.family_combo.findText(family)
        if idx >= 0:
            self.family_combo.setCurrentIndex(idx)
        elif self.family_combo.count():
            self.family_combo.setCurrentIndex(0)
        self.family_combo.currentTextChanged.connect(self._refresh_styles)

        self.style_combo = QComboBox()
        self._refresh_styles()
        current_style = existing.get(f"{field_key}_font_style",
                                      defaults.get("style", "Regular"))
        idx = self.style_combo.findText(current_style)
        if idx >= 0:
            self.style_combo.setCurrentIndex(idx)

        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(0.5, 3.0)
        self.scale_spin.setSingleStep(0.1)
        self.scale_spin.setSuffix("×")
        self.scale_spin.setValue(float(existing.get(f"{field_key}_font_scale",
                                                     defaults.get("scale", 1.0))))

        self.color_preview = QLabel()
        self.color_preview.setFixedSize(28, 20)
        self._update_color_preview()
        self.color_btn = QPushButton("Choose…")
        self.color_btn.clicked.connect(self._pick_color)

    def _refresh_styles(self) -> None:
        prev = self.style_combo.currentText() if self.style_combo.count() else "Regular"
        self.style_combo.blockSignals(True)
        self.style_combo.clear()
        self.style_combo.addItems(fonts_module.available_styles(
            self.family_combo.currentText()))
        idx = self.style_combo.findText(prev)
        if idx >= 0:
            self.style_combo.setCurrentIndex(idx)
        self.style_combo.blockSignals(False)

    def _update_color_preview(self) -> None:
        c = self._color
        self.color_preview.setStyleSheet(
            f"background-color: rgb({c.red()},{c.green()},{c.blue()});"
            f" border: 1px solid #444; border-radius: 3px;")

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(self._color, self.color_btn,
                                        f"{self.field_key.title()} colour")
        if color.isValid():
            self._color = color
            self._update_color_preview()

    def add_to_form(self, form: QFormLayout, label_prefix: str) -> None:
        color_row = QHBoxLayout()
        color_row.addWidget(self.color_preview)
        color_row.addWidget(self.color_btn)
        color_row.addStretch()
        form.addRow(f"{label_prefix} font:", self.family_combo)
        form.addRow(f"{label_prefix} style:", self.style_combo)
        form.addRow(f"{label_prefix} size:", self.scale_spin)
        form.addRow(f"{label_prefix} colour:", color_row)

    def to_dict(self) -> dict:
        return {
            f"{self.field_key}_font_family": self.family_combo.currentText(),
            f"{self.field_key}_font_style": self.style_combo.currentText(),
            f"{self.field_key}_font_scale": self.scale_spin.value(),
            f"{self.field_key}_color": [self._color.red(), self._color.green(),
                                         self._color.blue()],
        }


class CityDialog(QDialog):
    """Add or edit a single city marker. Split into tabs so the Basics tab
    stays uncluttered (name / location / colour / what to show), while
    Marker/Placement/Text/Notes get their own dedicated tabs."""

    MARKER_STYLES = [
        ("Dot (filled circle)", "dot"),
        ("Ring (hollow circle)", "ring"),
        ("Square", "square"),
        ("Diamond", "diamond"),
        ("Star", "star"),
    ]
    LABEL_SIDES = [
        ("Right of marker", "right"),
        ("Left of marker", "left"),
        ("Above marker", "top"),
        ("Below marker", "bottom"),
        ("Auto (whichever fits)", "auto"),
    ]

    # Sensible per-field styling defaults - name is bold and larger,
    # time is regular, weather is regular, notes is smaller and italic.
    _FIELD_DEFAULTS = {
        "name": {"family": fonts_module.DEFAULT_FAMILY, "style": "Bold",
                  "scale": 1.0, "color": [255, 255, 255]},
        "time": {"family": fonts_module.DEFAULT_FAMILY, "style": "Regular",
                  "scale": 1.0, "color": [255, 255, 255]},
        "weather": {"family": fonts_module.DEFAULT_FAMILY, "style": "Regular",
                     "scale": 0.9, "color": [180, 220, 255]},
        "notes": {"family": fonts_module.DEFAULT_FAMILY, "style": "Italic",
                    "scale": 0.85, "color": [200, 200, 200]},
    }

    # Named layout presets. The first entry is the default. Users pick one
    # from a dropdown; "Custom…" opens the row editor below for full control.
    LAYOUT_PRESETS = [
        ("Name + time / weather / notes  (default)",
         [["name", "time"], ["weather"], ["notes"]]),
        ("Everything on separate lines",
         [["name"], ["time"], ["weather"], ["notes"]]),
        ("Name / time + weather / notes",
         [["name"], ["time", "weather"], ["notes"]]),
        ("Name + time + weather (single row) / notes",
         [["name", "time", "weather"], ["notes"]]),
        ("Compact: name + time only",
         [["name", "time"]]),
        ("Name only",
         [["name"]]),
    ]

    def __init__(self, parent=None, existing: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Edit City" if existing else "Add City")
        # Five tabs across the top ("Basics", "Marker", "Placement",
        # "Layout", "Text styling") need more room than the previous 500px
        # allowed - the last tab labels were being elided on some styles
        # and several inner rows (font family + size + weight combos)
        # were cramped. 660 fits the full tab strip comfortably on every
        # platform theme I could test.
        self.setMinimumWidth(660)
        self.resize(720, 720)
        e = existing or {}
        self._marker_color = QColor(*e.get("color", [255, 210, 60]))

        layout = QVBoxLayout(self)

        search_label = QLabel(
            "Search the built-in city list, or fill in the Basics tab "
            "manually for anywhere not listed:")
        search_label.setWordWrap(True)
        layout.addWidget(search_label)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Type a city name…  (e.g. \"Paris\" or \"Japan\")")
        # Completer shows "City, Country" so identically-named places are
        # distinguishable. Model is the full label list; QCompleter does
        # its own case-insensitive substring filtering as the user types.
        self._city_labels = [city_label(c) for c in CITY_DATABASE]
        completer = QCompleter(self._city_labels)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        completer.setMaxVisibleItems(12)
        completer.activated.connect(self._on_pick_from_search)
        self.search_box.setCompleter(completer)
        # Enter in the box (without picking a completion) resolves the
        # top search hit, so keyboard users aren't forced to mouse into
        # the dropdown.
        self.search_box.returnPressed.connect(self._on_search_enter)
        layout.addWidget(self.search_box)

        tabs = QTabWidget()
        tabs.addTab(self._build_basics_tab(e), "Basics")
        tabs.addTab(self._build_marker_tab(e), "Marker")
        tabs.addTab(self._build_placement_tab(e), "Placement")
        tabs.addTab(self._wrap_scrollable(self._build_layout_tab(e)), "Layout")
        tabs.addTab(self._wrap_scrollable(self._build_text_tab(e)), "Text styling")
        tabs.addTab(self._build_weather_tab(e), "Weather")
        tabs.addTab(self._build_notes_tab(e), "Notes")
        layout.addWidget(tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _wrap_scrollable(self, widget: QWidget) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(widget)
        return scroll

    # -------------------------------------------------------- Basics tab
    def _build_basics_tab(self, e: dict) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        form = QFormLayout()
        self.name_edit = QLineEdit(e.get("name", ""))
        form.addRow("Display name:", self.name_edit)

        self.lat_spin = QDoubleSpinBox()
        self.lat_spin.setRange(-90, 90); self.lat_spin.setDecimals(2)
        self.lat_spin.setValue(e.get("lat", 0.0))
        form.addRow("Latitude:", self.lat_spin)

        self.lon_spin = QDoubleSpinBox()
        self.lon_spin.setRange(-180, 180); self.lon_spin.setDecimals(2)
        self.lon_spin.setValue(e.get("lon", 0.0))
        form.addRow("Longitude:", self.lon_spin)

        self.tz_combo = QComboBox()
        self.tz_combo.setEditable(True)
        self.tz_combo.addItems(ALL_TIMEZONES)
        tz = e.get("tz", "UTC")
        if tz in ALL_TIMEZONES:
            self.tz_combo.setCurrentText(tz)
        elif ALL_TIMEZONES:
            self.tz_combo.setCurrentText("UTC" if "UTC" in ALL_TIMEZONES else ALL_TIMEZONES[0])
        form.addRow("Timezone:", self.tz_combo)

        color_row = QHBoxLayout()
        self.color_preview = QLabel(); self.color_preview.setFixedSize(28, 20)
        self._update_marker_color_preview()
        color_btn = QPushButton("Choose colour…")
        color_btn.clicked.connect(self._pick_marker_color)
        color_row.addWidget(self.color_preview); color_row.addWidget(color_btn); color_row.addStretch()
        form.addRow("Marker colour:", color_row)

        layout.addLayout(form)

        # Single unified group for the four show/hide toggles - one setting
        # per feature, so there's no way to accidentally have "fetch on but
        # not displayed" or vice versa. This was the source of the "weather
        # not loading" bug before this rewrite.
        content_box = QGroupBox("What to show in the label")
        content_layout = QVBoxLayout(content_box)
        self.show_name_check = QCheckBox("City name")
        self.show_name_check.setChecked(e.get("show_name", True))
        self.show_time_check = QCheckBox("Local time")
        self.show_time_check.setChecked(e.get("show_time", True))
        self.show_weather_check = QCheckBox(
            "Current weather (temperature + condition, from Open-Meteo)")
        self.show_weather_check.setChecked(e.get("show_weather", False))
        self.show_notes_check = QCheckBox("Notes (edit them in the Notes tab)")
        self.show_notes_check.setChecked(e.get("show_notes", False))
        for ck in [self.show_name_check, self.show_time_check,
                    self.show_weather_check, self.show_notes_check]:
            content_layout.addWidget(ck)
        layout.addWidget(content_box)
        layout.addStretch()
        return w

    # -------------------------------------------------------- Marker tab
    def _build_marker_tab(self, e: dict) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self.marker_style_combo = QComboBox()
        for label, value in self.MARKER_STYLES:
            self.marker_style_combo.addItem(label, value)
        current_style = e.get("marker_style", "dot")
        idx = next((i for i, (_, v) in enumerate(self.MARKER_STYLES) if v == current_style), 0)
        self.marker_style_combo.setCurrentIndex(idx)
        form.addRow("Shape:", self.marker_style_combo)

        self.marker_size_spin = QDoubleSpinBox()
        self.marker_size_spin.setRange(0.4, 4.0); self.marker_size_spin.setSingleStep(0.1)
        self.marker_size_spin.setValue(float(e.get("marker_size", 1.0)))
        self.marker_size_spin.setSuffix("×")
        form.addRow("Size:", self.marker_size_spin)

        return w

    # -------------------------------------------------------- Placement tab
    def _build_placement_tab(self, e: dict) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        pos_box = QGroupBox("Label position relative to marker")
        pos_form = QFormLayout(pos_box)

        self.label_side_combo = QComboBox()
        for label, value in self.LABEL_SIDES:
            self.label_side_combo.addItem(label, value)
        side = e.get("label_side", "right")
        side_idx = next((i for i, (_, v) in enumerate(self.LABEL_SIDES) if v == side), 0)
        self.label_side_combo.setCurrentIndex(side_idx)
        pos_form.addRow("Side:", self.label_side_combo)

        self.offset_x_spin = QSpinBox()
        self.offset_x_spin.setRange(-500, 500); self.offset_x_spin.setSuffix(" px")
        self.offset_x_spin.setValue(int(e.get("label_offset_x", 0)))
        self.offset_x_spin.setToolTip("Extra horizontal nudge from the automatic position")
        pos_form.addRow("Nudge X:", self.offset_x_spin)

        self.offset_y_spin = QSpinBox()
        self.offset_y_spin.setRange(-500, 500); self.offset_y_spin.setSuffix(" px")
        self.offset_y_spin.setValue(int(e.get("label_offset_y", 0)))
        self.offset_y_spin.setToolTip("Extra vertical nudge from the automatic position")
        pos_form.addRow("Nudge Y:", self.offset_y_spin)

        layout.addWidget(pos_box)

        bg_box = QGroupBox("Label background")
        bg_layout = QHBoxLayout(bg_box)
        self.bg_alpha_slider = ClickJumpSlider(Qt.Horizontal)
        self.bg_alpha_slider.setRange(0, 255)
        self.bg_alpha_slider.setValue(int(e.get("background_alpha", 165)))
        self.bg_alpha_value = QLabel(str(self.bg_alpha_slider.value()))
        self.bg_alpha_value.setMinimumWidth(32)
        self.bg_alpha_slider.valueChanged.connect(
            lambda v: self.bg_alpha_value.setText(str(v)))
        bg_layout.addWidget(QLabel("Transparent"))
        bg_layout.addWidget(self.bg_alpha_slider)
        bg_layout.addWidget(QLabel("Solid"))
        bg_layout.addWidget(self.bg_alpha_value)
        layout.addWidget(bg_box)

        layout.addStretch()
        return w

    # -------------------------------------------------------- Text styling tab
    def _build_text_tab(self, e: dict) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        intro = QLabel(
            "Each part of the label can have its own font, style, size, "
            "and colour. Size is a multiplier of the auto-sized base "
            "font (which scales with your wallpaper resolution).")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._field_controls: dict[str, _FieldStyleControls] = {}
        for field, human_label in [("name", "Name"), ("time", "Time"),
                                     ("weather", "Weather"), ("notes", "Notes")]:
            box = QGroupBox(f"{human_label} text")
            form = QFormLayout(box)
            ctrl = _FieldStyleControls(w, field, self._FIELD_DEFAULTS[field], e)
            ctrl.add_to_form(form, "")
            self._field_controls[field] = ctrl
            layout.addWidget(box)

        layout.addStretch()
        return w

    # -------------------------------------------------------- Layout tab
    def _build_layout_tab(self, e: dict) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        intro = QLabel(
            "Choose how the label's parts are arranged. Pick a preset for "
            "a quick setup, or select \"Custom…\" to build your own layout "
            "row by row.\n\n"
            "Each row contains one or more fields that render side by "
            "side. Fields not enabled on the Basics tab are automatically "
            "skipped.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        preset_box = QGroupBox("Preset")
        preset_layout = QVBoxLayout(preset_box)
        self.layout_preset_combo = QComboBox()
        for label, _rows in self.LAYOUT_PRESETS:
            self.layout_preset_combo.addItem(label)
        self.layout_preset_combo.addItem("Custom…")

        existing_layout = e.get("label_layout")
        # Match the existing layout against known presets so users returning
        # to the dialog see their choice pre-selected.
        matched_idx = self.layout_preset_combo.count() - 1  # Custom by default
        for i, (_, rows) in enumerate(self.LAYOUT_PRESETS):
            if existing_layout == rows:
                matched_idx = i
                break
        if existing_layout is None:
            matched_idx = 0
        self.layout_preset_combo.setCurrentIndex(matched_idx)
        self.layout_preset_combo.currentIndexChanged.connect(self._on_layout_preset_changed)
        preset_layout.addWidget(self.layout_preset_combo)
        layout.addWidget(preset_box)

        # Custom row editor: 4 rows, each with 4 checkboxes (name/time/
        # weather/notes). Kept simple - a full drag-and-drop editor would
        # be nicer but is a lot more code for a marginal usability win.
        custom_box = QGroupBox("Custom rows (only used when preset is \"Custom…\")")
        custom_layout = QVBoxLayout(custom_box)
        custom_layout.addWidget(QLabel(
            "Tick which fields appear on each row (top row is drawn first):"))

        self._row_checkboxes: list[dict[str, QCheckBox]] = []
        header_row = QHBoxLayout()
        header_row.addWidget(QLabel("     "))  # row label spacer
        for field in ["Name", "Time", "Weather", "Notes"]:
            lbl = QLabel(field); lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("color:#888;")
            header_row.addWidget(lbl)
        custom_layout.addLayout(header_row)

        # Initialise from existing custom layout, or empty if this is
        # coming from a preset choice.
        seed_layout = existing_layout if matched_idx == self.layout_preset_combo.count() - 1 else []

        for row_idx in range(4):
            row_layout = QHBoxLayout()
            row_layout.addWidget(QLabel(f"Row {row_idx + 1}:"))
            row_checks = {}
            for field in ["name", "time", "weather", "notes"]:
                ck = QCheckBox()
                is_set = (row_idx < len(seed_layout)
                            and field in seed_layout[row_idx])
                ck.setChecked(is_set)
                row_layout.addWidget(ck, alignment=Qt.AlignCenter)
                row_checks[field] = ck
            custom_layout.addLayout(row_layout)
            self._row_checkboxes.append(row_checks)

        layout.addWidget(custom_box)
        layout.addStretch()
        return w

    def _on_layout_preset_changed(self, idx: int) -> None:
        # If the user picked a named preset, populate the custom editor
        # to match so switching to Custom later is a small tweak, not a
        # fresh start.
        if idx < len(self.LAYOUT_PRESETS):
            _, rows = self.LAYOUT_PRESETS[idx]
            for row_idx, row_checks in enumerate(self._row_checkboxes):
                row_fields = rows[row_idx] if row_idx < len(rows) else []
                for field, ck in row_checks.items():
                    ck.setChecked(field in row_fields)

    def _current_layout(self) -> list[list[str]]:
        """Read the resolved layout from either the preset selector or the
        custom row grid, depending on which is active."""
        idx = self.layout_preset_combo.currentIndex()
        if idx < len(self.LAYOUT_PRESETS):
            return [list(row) for row in self.LAYOUT_PRESETS[idx][1]]
        # Custom - read the checkboxes
        result: list[list[str]] = []
        for row_checks in self._row_checkboxes:
            row = [field for field, ck in row_checks.items() if ck.isChecked()]
            if row:
                result.append(row)
        return result or [["name"]]

    # -------------------------------------------------------- Weather tab
    def _build_weather_tab(self, e: dict) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        layout.addWidget(QLabel(
            "Enable/disable weather via the Basics tab. This tab controls "
            "what the weather line LOOKS like when it's shown."))

        parts_box = QGroupBox("Show which parts")
        parts_layout = QVBoxLayout(parts_box)
        self.weather_show_emoji_check = QCheckBox("Weather icon (☀ 🌧 ⛅ etc.)")
        self.weather_show_emoji_check.setChecked(e.get("weather_show_emoji", True))
        self.weather_show_temp_check = QCheckBox("Temperature (e.g. 24°C)")
        self.weather_show_temp_check.setChecked(e.get("weather_show_temp", True))
        self.weather_show_label_check = QCheckBox(
            "Condition label (e.g. \"Clear\", \"Rain\") — see custom names below")
        self.weather_show_label_check.setChecked(e.get("weather_show_label", True))
        for ck in (self.weather_show_emoji_check, self.weather_show_temp_check,
                    self.weather_show_label_check):
            parts_layout.addWidget(ck)
        layout.addWidget(parts_box)

        # Custom label mapping table - swap default condition names for
        # your own preferred wording. Users can leave the "override" blank
        # to keep the default.
        rename_box = QGroupBox("Rename weather conditions")
        rename_layout = QVBoxLayout(rename_box)
        rename_layout.addWidget(QLabel(
            "Replace the default names with anything you like — say "
            "\"Sunny\" instead of \"Clear\", \"Wet\" instead of \"Rain\", etc. "
            "Blank overrides keep the default name."))

        # A small set of the most-used conditions (the full WMO list is
        # long; users can drop through to the "Custom format" field below
        # if they need finer control).
        self._weather_label_overrides: list[tuple[str, QLineEdit]] = []
        existing_map = e.get("weather_label_map") or {}
        common = ["Clear", "Mostly clear", "Partly cloudy", "Overcast",
                   "Fog", "Light rain", "Rain", "Heavy rain",
                   "Light snow", "Snow", "Thunderstorm"]
        form = QFormLayout()
        for cond in common:
            edit = QLineEdit()
            edit.setPlaceholderText(cond)
            if cond in existing_map:
                edit.setText(existing_map[cond])
            form.addRow(f"{cond}:", edit)
            self._weather_label_overrides.append((cond, edit))
        rename_layout.addLayout(form)
        layout.addWidget(rename_box)

        # Full custom format string for power users.
        custom_box = QGroupBox("Custom format (advanced)")
        custom_layout = QVBoxLayout(custom_box)
        custom_layout.addWidget(QLabel(
            "Override the whole weather line with a template. Placeholders:\n"
            "  {emoji}  {temp}  {label}  {code}\n"
            "Example: \"{emoji} it's {temp} and {label} outside\"\n"
            "Leave blank to use the parts selected above."))
        self.weather_custom_format_edit = QLineEdit(
            e.get("weather_custom_format", ""))
        self.weather_custom_format_edit.setPlaceholderText(
            "Leave blank to use the default composition")
        custom_layout.addWidget(self.weather_custom_format_edit)
        layout.addWidget(custom_box)

        layout.addStretch()
        return w

    # -------------------------------------------------------- Notes tab
    def _build_notes_tab(self, e: dict) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel(
            "Free text shown under the marker (e.g. \"Sarah's flat\", "
            "\"office hours 9-5\"). Notes are wrapped and capped at "
            "3 lines so they don't dominate the map.\n\n"
            "Enable/disable notes display via the Basics tab checkbox."))
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setPlainText(e.get("notes", ""))
        self.notes_edit.setPlaceholderText("Optional…")
        layout.addWidget(self.notes_edit)
        return w

    # -------------------------------------------------------- helpers
    def _update_marker_color_preview(self) -> None:
        c = self._marker_color
        self.color_preview.setStyleSheet(
            f"background-color: rgb({c.red()},{c.green()},{c.blue()});"
            f" border: 1px solid #444; border-radius: 3px;")

    def _pick_marker_color(self) -> None:
        color = QColorDialog.getColor(self._marker_color, self, "Marker colour")
        if color.isValid():
            self._marker_color = color
            self._update_marker_color_preview()

    def _on_pick_from_search(self, text: str) -> None:
        """User chose an entry from the completer dropdown. `text` is a
        'City, Country' label; resolve it back to the database row and
        fill in the Basics fields."""
        city = find_city(text)
        if city is None:
            city = search_cities(text, limit=1)
            city = city[0] if city else None
        if city is None:
            return
        name, country, lat, lon, tz = city
        self.name_edit.setText(name)
        self.lat_spin.setValue(lat)
        self.lon_spin.setValue(lon)
        if tz in ALL_TIMEZONES:
            self.tz_combo.setCurrentText(tz)

    def _on_search_enter(self) -> None:
        """Enter pressed in the search box without picking a dropdown
        row: resolve the best match for whatever's typed so far."""
        text = self.search_box.text().strip()
        if not text:
            return
        results = search_cities(text, limit=1)
        if results:
            self._on_pick_from_search(city_label(results[0]))

    def result_city(self) -> dict:
        # Collect any non-blank weather label overrides.
        label_map = {}
        for cond, edit in self._weather_label_overrides:
            override = edit.text().strip()
            if override:
                label_map[cond] = override

        result = {
            # basics
            "name": self.name_edit.text().strip() or "Unnamed",
            "lat": self.lat_spin.value(),
            "lon": self.lon_spin.value(),
            "tz": self.tz_combo.currentText().strip() or "UTC",
            "color": [self._marker_color.red(), self._marker_color.green(), self._marker_color.blue()],
            # marker
            "marker_style": self.marker_style_combo.currentData(),
            "marker_size": self.marker_size_spin.value(),
            # placement
            "label_side": self.label_side_combo.currentData(),
            "label_offset_x": self.offset_x_spin.value(),
            "label_offset_y": self.offset_y_spin.value(),
            "background_alpha": self.bg_alpha_slider.value(),
            # what to show
            "show_name": self.show_name_check.isChecked(),
            "show_time": self.show_time_check.isChecked(),
            "show_weather": self.show_weather_check.isChecked(),
            "show_notes": self.show_notes_check.isChecked(),
            # layout
            "label_layout": self._current_layout(),
            # weather formatting
            "weather_show_emoji": self.weather_show_emoji_check.isChecked(),
            "weather_show_temp": self.weather_show_temp_check.isChecked(),
            "weather_show_label": self.weather_show_label_check.isChecked(),
            "weather_label_map": label_map,
            "weather_custom_format": self.weather_custom_format_edit.text().strip(),
            # notes
            "notes": self.notes_edit.toPlainText().strip(),
        }
        # Merge in per-field font/style/size/color for each of name/time/weather/notes.
        for ctrl in self._field_controls.values():
            result.update(ctrl.to_dict())
        return result
