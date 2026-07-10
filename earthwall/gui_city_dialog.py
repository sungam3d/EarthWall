from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QCompleter, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QScrollArea, QSlider, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget,
)

from .city_database import CITY_DATABASE, search_cities
from . import fonts as fonts_module

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

    def __init__(self, parent=None, existing: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Edit City" if existing else "Add City")
        self.setMinimumWidth(500)
        self.resize(520, 640)
        e = existing or {}
        self._marker_color = QColor(*e.get("color", [255, 210, 60]))

        layout = QVBoxLayout(self)

        search_label = QLabel(
            "Search the built-in city list, or fill in the Basics tab "
            "manually for anywhere not listed:")
        search_label.setWordWrap(True)
        layout.addWidget(search_label)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Type a city name…")
        completer_names = sorted({c[0] for c in CITY_DATABASE})
        completer = QCompleter(completer_names)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.activated.connect(self._on_pick_from_search)
        self.search_box.setCompleter(completer)
        layout.addWidget(self.search_box)

        tabs = QTabWidget()
        tabs.addTab(self._build_basics_tab(e), "Basics")
        tabs.addTab(self._build_marker_tab(e), "Marker")
        tabs.addTab(self._build_placement_tab(e), "Placement")
        tabs.addTab(self._wrap_scrollable(self._build_text_tab(e)), "Text styling")
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
        self.bg_alpha_slider = QSlider(Qt.Horizontal)
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
        matches = [c for c in CITY_DATABASE if c[0] == text]
        if not matches:
            matches = search_cities(text, limit=1)
        if not matches:
            return
        name, country, lat, lon, tz = matches[0]
        self.name_edit.setText(name)
        self.lat_spin.setValue(lat)
        self.lon_spin.setValue(lon)
        if tz in ALL_TIMEZONES:
            self.tz_combo.setCurrentText(tz)

    def result_city(self) -> dict:
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
            # notes
            "notes": self.notes_edit.toPlainText().strip(),
        }
        # Merge in per-field font/style/size/color for each of name/time/weather/notes.
        for ctrl in self._field_controls.values():
            result.update(ctrl.to_dict())
        return result
