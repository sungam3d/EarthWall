from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QCompleter, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QSlider, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from .city_database import CITY_DATABASE, search_cities

try:
    from zoneinfo import available_timezones
    ALL_TIMEZONES = sorted(available_timezones())
except Exception:
    ALL_TIMEZONES = []


class CityDialog(QDialog):
    """Add or edit a single city marker. Basics tab has the fields you need
    99% of the time (name / location / timezone / colour); the other tabs
    are for finer control: label placement, marker style, weather display,
    and notes. Everything falls back to a sensible default when omitted,
    so old configs keep working without changes."""

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

    def __init__(self, parent=None, existing: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Edit City" if existing else "Add City")
        self.setMinimumWidth(460)
        e = existing or {}
        self._marker_color = QColor(*e.get("color", [255, 210, 60]))
        self._text_color = QColor(*e.get("text_color", [255, 255, 255]))

        layout = QVBoxLayout(self)

        search_label = QLabel(
            "Search the built-in city list, or just fill in the Basics tab "
            "manually for anywhere not listed:"
        )
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
        tabs.addTab(self._build_display_tab(e), "Display")
        tabs.addTab(self._build_weather_tab(e), "Weather")
        tabs.addTab(self._build_notes_tab(e), "Notes")
        layout.addWidget(tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # -------------------------------------------------------- Basics tab
    def _build_basics_tab(self, e: dict) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

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

        return w

    # -------------------------------------------------------- Display tab
    def _build_display_tab(self, e: dict) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        # --- Marker style ---
        marker_box = QGroupBox("Marker")
        marker_form = QFormLayout(marker_box)

        self.marker_style_combo = QComboBox()
        for label, value in self.MARKER_STYLES:
            self.marker_style_combo.addItem(label, value)
        current_style = e.get("marker_style", "dot")
        idx = next((i for i, (_, v) in enumerate(self.MARKER_STYLES) if v == current_style), 0)
        self.marker_style_combo.setCurrentIndex(idx)
        marker_form.addRow("Shape:", self.marker_style_combo)

        self.marker_size_spin = QDoubleSpinBox()
        self.marker_size_spin.setRange(0.4, 4.0); self.marker_size_spin.setSingleStep(0.1)
        self.marker_size_spin.setValue(float(e.get("marker_size", 1.0)))
        self.marker_size_spin.setSuffix("×")
        marker_form.addRow("Size:", self.marker_size_spin)

        layout.addWidget(marker_box)

        # --- Label placement ---
        label_box = QGroupBox("Label placement")
        label_form = QFormLayout(label_box)

        self.label_side_combo = QComboBox()
        for label, value in self.LABEL_SIDES:
            self.label_side_combo.addItem(label, value)
        side = e.get("label_side", "right")
        side_idx = next((i for i, (_, v) in enumerate(self.LABEL_SIDES) if v == side), 0)
        self.label_side_combo.setCurrentIndex(side_idx)
        label_form.addRow("Position:", self.label_side_combo)

        self.offset_x_spin = QSpinBox()
        self.offset_x_spin.setRange(-500, 500); self.offset_x_spin.setSuffix(" px")
        self.offset_x_spin.setValue(int(e.get("label_offset_x", 0)))
        self.offset_x_spin.setToolTip("Extra horizontal nudge from the automatic position")
        label_form.addRow("Nudge X:", self.offset_x_spin)

        self.offset_y_spin = QSpinBox()
        self.offset_y_spin.setRange(-500, 500); self.offset_y_spin.setSuffix(" px")
        self.offset_y_spin.setValue(int(e.get("label_offset_y", 0)))
        self.offset_y_spin.setToolTip("Extra vertical nudge from the automatic position")
        label_form.addRow("Nudge Y:", self.offset_y_spin)

        self.label_scale_spin = QDoubleSpinBox()
        self.label_scale_spin.setRange(0.5, 3.0); self.label_scale_spin.setSingleStep(0.1)
        self.label_scale_spin.setValue(float(e.get("label_scale", 1.0)))
        self.label_scale_spin.setSuffix("×")
        label_form.addRow("Font size:", self.label_scale_spin)

        layout.addWidget(label_box)

        # --- Label contents & style ---
        content_box = QGroupBox("What to show in the label")
        content_layout = QVBoxLayout(content_box)
        self.show_name_check = QCheckBox("City name")
        self.show_name_check.setChecked(e.get("show_name", True))
        self.show_time_check = QCheckBox("Local time")
        self.show_time_check.setChecked(e.get("show_time", True))
        self.show_weather_check = QCheckBox("Weather (see Weather tab to enable fetching)")
        self.show_weather_check.setChecked(e.get("show_weather", False))
        self.show_notes_check = QCheckBox("Notes (see Notes tab)")
        self.show_notes_check.setChecked(e.get("show_notes", False))
        for ck in [self.show_name_check, self.show_time_check,
                    self.show_weather_check, self.show_notes_check]:
            content_layout.addWidget(ck)
        layout.addWidget(content_box)

        # --- Colors & background ---
        style_box = QGroupBox("Label style")
        style_form = QFormLayout(style_box)

        text_color_row = QHBoxLayout()
        self.text_color_preview = QLabel(); self.text_color_preview.setFixedSize(28, 20)
        self._update_text_color_preview()
        text_color_btn = QPushButton("Choose…")
        text_color_btn.clicked.connect(self._pick_text_color)
        text_color_row.addWidget(self.text_color_preview)
        text_color_row.addWidget(text_color_btn); text_color_row.addStretch()
        style_form.addRow("Text colour:", text_color_row)

        bg_row = QHBoxLayout()
        self.bg_alpha_slider = QSlider(Qt.Horizontal)
        self.bg_alpha_slider.setRange(0, 255)
        self.bg_alpha_slider.setValue(int(e.get("background_alpha", 165)))
        self.bg_alpha_value = QLabel(str(self.bg_alpha_slider.value()))
        self.bg_alpha_value.setMinimumWidth(32)
        self.bg_alpha_slider.valueChanged.connect(
            lambda v: self.bg_alpha_value.setText(str(v)))
        bg_row.addWidget(QLabel("Transparent"))
        bg_row.addWidget(self.bg_alpha_slider)
        bg_row.addWidget(QLabel("Solid"))
        bg_row.addWidget(self.bg_alpha_value)
        style_form.addRow("Background:", bg_row)

        layout.addWidget(style_box)
        layout.addStretch()
        return w

    # -------------------------------------------------------- Weather tab
    def _build_weather_tab(self, e: dict) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        info = QLabel(
            "Weather is fetched from Open-Meteo (free, no account needed) "
            "using the city's latitude/longitude, and cached per city for "
            "15 minutes.\n\n"
            "To actually see it on the map, also enable "
            "\"Weather\" in the Display tab's \"What to show in the label\" section."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.enable_weather_check = QCheckBox(
            "Fetch weather for this city (uses ~1 API call every 15 minutes)")
        self.enable_weather_check.setChecked(e.get("show_weather", False))
        # Keep the two checkboxes in sync - enabling here also flips the
        # display checkbox on, so a novice doesn't have to know it's a
        # two-step process; they can uncheck the display one later if they
        # want to fetch without displaying.
        self.enable_weather_check.toggled.connect(self.show_weather_check.setChecked)
        layout.addWidget(self.enable_weather_check)

        note = QLabel(
            "Note: this app doesn't use an API key, so weather may fail "
            "silently if the free service is temporarily rate-limiting. "
            "The last known good reading is kept in the meantime."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#888; font-size:11px;")
        layout.addWidget(note)

        layout.addStretch()
        return w

    # -------------------------------------------------------- Notes tab
    def _build_notes_tab(self, e: dict) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel(
            "Free text shown under the marker (e.g. \"Sarah's flat\", "
            "\"office hours 9-5\"). Kept short - long notes are wrapped and "
            "capped at a few lines so they don't dominate the map."))
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setPlainText(e.get("notes", ""))
        self.notes_edit.setPlaceholderText("Optional…")
        layout.addWidget(self.notes_edit)

        self.show_notes_on_map_check = QCheckBox(
            "Show these notes on the map (also toggleable in Display tab)")
        self.show_notes_on_map_check.setChecked(e.get("show_notes", False))
        self.show_notes_on_map_check.toggled.connect(self.show_notes_check.setChecked)
        layout.addWidget(self.show_notes_on_map_check)
        return w

    # -------------------------------------------------------- helpers
    def _update_marker_color_preview(self) -> None:
        c = self._marker_color
        self.color_preview.setStyleSheet(
            f"background-color: rgb({c.red()},{c.green()},{c.blue()});"
            f" border: 1px solid #444; border-radius: 3px;")

    def _update_text_color_preview(self) -> None:
        c = self._text_color
        self.text_color_preview.setStyleSheet(
            f"background-color: rgb({c.red()},{c.green()},{c.blue()});"
            f" border: 1px solid #444; border-radius: 3px;")

    def _pick_marker_color(self) -> None:
        color = QColorDialog.getColor(self._marker_color, self, "Marker colour")
        if color.isValid():
            self._marker_color = color
            self._update_marker_color_preview()

    def _pick_text_color(self) -> None:
        color = QColorDialog.getColor(self._text_color, self, "Text colour")
        if color.isValid():
            self._text_color = color
            self._update_text_color_preview()

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
        return {
            # basics
            "name": self.name_edit.text().strip() or "Unnamed",
            "lat": self.lat_spin.value(),
            "lon": self.lon_spin.value(),
            "tz": self.tz_combo.currentText().strip() or "UTC",
            "color": [self._marker_color.red(), self._marker_color.green(), self._marker_color.blue()],
            # marker style
            "marker_style": self.marker_style_combo.currentData(),
            "marker_size": self.marker_size_spin.value(),
            # label placement
            "label_side": self.label_side_combo.currentData(),
            "label_offset_x": self.offset_x_spin.value(),
            "label_offset_y": self.offset_y_spin.value(),
            "label_scale": self.label_scale_spin.value(),
            # label contents
            "show_name": self.show_name_check.isChecked(),
            "show_time": self.show_time_check.isChecked(),
            "show_weather": self.show_weather_check.isChecked(),
            "show_notes": self.show_notes_check.isChecked(),
            # label styling
            "text_color": [self._text_color.red(), self._text_color.green(), self._text_color.blue()],
            "background_alpha": self.bg_alpha_slider.value(),
            # notes
            "notes": self.notes_edit.toPlainText().strip(),
        }
