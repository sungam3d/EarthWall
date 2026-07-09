from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QColorDialog,
    QCompleter,
)

from .city_database import CITY_DATABASE, search_cities

try:
    from zoneinfo import available_timezones
    ALL_TIMEZONES = sorted(available_timezones())
except Exception:
    ALL_TIMEZONES = []


class CityDialog(QDialog):
    """Add or edit a single city marker. Picking a name from the search
    box auto-fills lat/lon/timezone from the built-in database; all
    fields stay editable afterwards for full manual control."""

    def __init__(self, parent=None, existing: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Edit City" if existing else "Add City")
        self.setMinimumWidth(360)
        self._color = QColor(*(existing.get("color", [255, 210, 60]) if existing else [255, 210, 60]))

        layout = QVBoxLayout(self)

        search_label = QLabel(
            "Search the built-in city list, or just fill in the fields "
            "below manually for anywhere not listed:"
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

        form = QFormLayout()

        self.name_edit = QLineEdit(existing["name"] if existing else "")
        form.addRow("Display name:", self.name_edit)

        self.lat_spin = QDoubleSpinBox()
        self.lat_spin.setRange(-90, 90)
        self.lat_spin.setDecimals(2)
        self.lat_spin.setValue(existing["lat"] if existing else 0.0)
        form.addRow("Latitude:", self.lat_spin)

        self.lon_spin = QDoubleSpinBox()
        self.lon_spin.setRange(-180, 180)
        self.lon_spin.setDecimals(2)
        self.lon_spin.setValue(existing["lon"] if existing else 0.0)
        form.addRow("Longitude:", self.lon_spin)

        self.tz_combo = QComboBox()
        self.tz_combo.setEditable(True)
        self.tz_combo.addItems(ALL_TIMEZONES)
        if existing and existing.get("tz") in ALL_TIMEZONES:
            self.tz_combo.setCurrentText(existing["tz"])
        elif ALL_TIMEZONES:
            self.tz_combo.setCurrentText("UTC" if "UTC" in ALL_TIMEZONES else ALL_TIMEZONES[0])
        form.addRow("Timezone:", self.tz_combo)

        color_row = QHBoxLayout()
        self.color_preview = QLabel()
        self.color_preview.setFixedSize(28, 20)
        self._update_color_preview()
        color_btn = QPushButton("Choose colour…")
        color_btn.clicked.connect(self._pick_color)
        color_row.addWidget(self.color_preview)
        color_row.addWidget(color_btn)
        color_row.addStretch()
        form.addRow("Marker colour:", color_row)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_color_preview(self) -> None:
        self.color_preview.setStyleSheet(
            f"background-color: rgb({self._color.red()},{self._color.green()},{self._color.blue()}); "
            f"border: 1px solid #444; border-radius: 3px;"
        )

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(self._color, self, "Marker colour")
        if color.isValid():
            self._color = color
            self._update_color_preview()

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
            "name": self.name_edit.text().strip() or "Unnamed",
            "lat": self.lat_spin.value(),
            "lon": self.lon_spin.value(),
            "tz": self.tz_combo.currentText().strip() or "UTC",
            "color": [self._color.red(), self._color.green(), self._color.blue()],
        }
