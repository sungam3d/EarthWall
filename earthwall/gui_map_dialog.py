from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout,
)

from . import maps as maps_module


class ImportMapDialog(QDialog):
    """Import a custom day map (required) and optionally a matching night
    map. If no night map is supplied, the bundled default city-lights map
    is paired with it automatically so night still looks reasonable."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import a New Map")
        self.setMinimumWidth(420)
        self.day_path: str | None = None
        self.night_path: str | None = None

        layout = QVBoxLayout(self)

        info = QLabel(
            "Pick a flat (equirectangular, roughly 2:1 width:height) image "
            "for the daytime view. Almost any image format works - JPEG, "
            "PNG, BMP, TIFF, WEBP.\n\n"
            "A night map is optional - if you skip it, the default city-"
            "lights map will be used at night instead."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. My Custom Map")
        form.addRow("Map name:", self.name_edit)
        layout.addLayout(form)

        day_row = QHBoxLayout()
        self.day_label = QLabel("No file selected")
        day_btn = QPushButton("Choose day image…")
        day_btn.clicked.connect(self._pick_day)
        day_row.addWidget(QLabel("Day map:"))
        day_row.addWidget(self.day_label, stretch=1)
        day_row.addWidget(day_btn)
        layout.addLayout(day_row)

        night_row = QHBoxLayout()
        self.night_label = QLabel("(optional - default will be used)")
        night_btn = QPushButton("Choose night image…")
        night_btn.clicked.connect(self._pick_night)
        night_row.addWidget(QLabel("Night map:"))
        night_row.addWidget(self.night_label, stretch=1)
        night_row.addWidget(night_btn)
        layout.addLayout(night_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _image_filter(self) -> str:
        return "Images (*.jpg *.jpeg *.png *.bmp *.tiff *.tif *.webp *.gif);;All files (*)"

    def _pick_day(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose day map image", "", self._image_filter())
        if path:
            self.day_path = path
            self.day_label.setText(path.split("/")[-1])
            if not maps_module.check_aspect_ratio(path):
                QMessageBox.information(
                    self, "Unusual aspect ratio",
                    "That image isn't close to the standard 2:1 (width:height) "
                    "equirectangular ratio. It'll still be used, but may look "
                    "stretched on the wallpaper.",
                )

    def _pick_night(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose night map image", "", self._image_filter())
        if path:
            self.night_path = path
            self.night_label.setText(path.split("/")[-1])

    def _on_accept(self) -> None:
        if not self.day_path:
            QMessageBox.warning(self, "Missing day image", "Please choose a day map image first.")
            return
        if not self.name_edit.text().strip():
            self.name_edit.setText("Custom Map")
        self.accept()

    def result_values(self) -> tuple[str, str, str | None]:
        return self.name_edit.text().strip(), self.day_path, self.night_path
