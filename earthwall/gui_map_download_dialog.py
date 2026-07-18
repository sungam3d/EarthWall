"""
Dialog for browsing and downloading extra NASA map sets on demand.

Grouped by category with a checkbox per map, a "download selected" button,
and a progress bar. Downloads run on a background QThread so the UI stays
responsive, and each finished map is immediately available to render.
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from . import map_downloads


class _DownloadWorker(QThread):
    """Downloads a list of catalog items one after another, emitting
    progress and per-item completion so the dialog can update."""
    item_progress = Signal(str, float, str)   # item_id, fraction, message
    item_done = Signal(str, bool, str)         # item_id, ok, error_message
    all_done = Signal()

    def __init__(self, items: list[dict]):
        super().__init__()
        self._items = items
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        for item in self._items:
            if self._cancelled:
                break
            item_id = item["id"]

            def _prog(frac, msg, _id=item_id):
                self.item_progress.emit(_id, frac, msg)

            try:
                map_downloads.download_map_set(item, progress=_prog)
                self.item_done.emit(item_id, True, "")
            except map_downloads.MapDownloadError as e:
                self.item_done.emit(item_id, False, str(e))
            except Exception as e:  # noqa: BLE001 - surface anything to UI
                self.item_done.emit(item_id, False, f"Unexpected error: {e}")
        self.all_done.emit()


class MapDownloadDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Download NASA Maps")
        self.setMinimumWidth(560)
        self.resize(600, 640)
        self._checks: dict[str, QCheckBox] = {}
        self._status_labels: dict[str, QLabel] = {}
        self._items_by_id: dict[str, dict] = {}
        self._worker: _DownloadWorker | None = None
        self._downloaded_any = False

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Extra Earth maps from NASA's Earth Observatory. The app ships "
            "with just two maps to stay small - download any of these only "
            "if you want them. Each is a public-domain image, roughly "
            "1-3 MB, sharp enough for 4K.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # Scrollable body grouped by category.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        body_layout = QVBoxLayout(body)

        catalog = map_downloads.all_catalog()
        have = set()
        try:
            from . import maps as maps_module
            have = set(maps_module.list_map_sets().keys())
        except Exception:
            pass

        # Group by category, preserving first-seen order.
        categories: list[str] = []
        by_cat: dict[str, list[dict]] = {}
        for item in catalog:
            cat = item["category"]
            if cat not in by_cat:
                by_cat[cat] = []
                categories.append(cat)
            by_cat[cat].append(item)
            self._items_by_id[item["id"]] = item

        for cat in categories:
            box = QGroupBox(cat)
            box_layout = QVBoxLayout(box)
            for item in by_cat[cat]:
                row = QHBoxLayout()
                already = item["id"] in have
                ck = QCheckBox(f"{item['name']}  (~{item['approx_mb']} MB)")
                ck.setEnabled(not already)
                self._checks[item["id"]] = ck
                row.addWidget(ck, stretch=1)

                status = QLabel("✓ installed" if already else "")
                status.setStyleSheet("color:#6a9a6a;" if already else "color:#888;")
                self._status_labels[item["id"]] = status
                row.addWidget(status)
                box_layout.addLayout(row)

                credit = QLabel(item.get("credit", ""))
                credit.setStyleSheet("color:#777; font-size:10px;")
                box_layout.addWidget(credit)
            body_layout.addWidget(box)

        body_layout.addStretch()
        scroll.setWidget(body)
        layout.addWidget(scroll, stretch=1)

        # Select-all / none helpers.
        sel_row = QHBoxLayout()
        all_btn = QPushButton("Select all available")
        all_btn.clicked.connect(lambda: self._set_all(True))
        none_btn = QPushButton("Select none")
        none_btn.clicked.connect(lambda: self._set_all(False))
        sel_row.addWidget(all_btn)
        sel_row.addWidget(none_btn)
        sel_row.addStretch()
        layout.addLayout(sel_row)

        # Progress + status line.
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.status_line = QLabel("")
        self.status_line.setWordWrap(True)
        layout.addWidget(self.status_line)

        # Buttons.
        self.button_box = QDialogButtonBox()
        self.download_button = self.button_box.addButton(
            "Download selected", QDialogButtonBox.AcceptRole)
        self.download_button.clicked.connect(self._start_download)
        self.close_button = self.button_box.addButton(QDialogButtonBox.Close)
        self.close_button.clicked.connect(self.reject)
        layout.addWidget(self.button_box)

    def _set_all(self, state: bool) -> None:
        for ck in self._checks.values():
            if ck.isEnabled():
                ck.setChecked(state)

    def _selected_items(self) -> list[dict]:
        return [self._items_by_id[i] for i, ck in self._checks.items()
                if ck.isEnabled() and ck.isChecked()]

    def _start_download(self) -> None:
        items = self._selected_items()
        if not items:
            self.status_line.setText("Nothing selected - tick a map above first.")
            return
        # Lock the UI while downloading.
        self.download_button.setEnabled(False)
        self.close_button.setEnabled(False)
        for ck in self._checks.values():
            ck.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self._total = len(items)
        self._completed = 0

        self._worker = _DownloadWorker(items)
        self._worker.item_progress.connect(self._on_item_progress)
        self._worker.item_done.connect(self._on_item_done)
        self._worker.all_done.connect(self._on_all_done)
        self._worker.start()

    def _on_item_progress(self, item_id: str, frac: float, msg: str) -> None:
        # Overall progress = completed items + current item fraction.
        overall = (self._completed + frac) / max(1, self._total)
        self.progress.setValue(int(overall * 100))
        self.status_line.setText(msg)

    def _on_item_done(self, item_id: str, ok: bool, error: str) -> None:
        self._completed += 1
        lbl = self._status_labels.get(item_id)
        if ok:
            self._downloaded_any = True
            if lbl:
                lbl.setText("✓ downloaded")
                lbl.setStyleSheet("color:#6a9a6a;")
        else:
            if lbl:
                lbl.setText("✗ failed")
                lbl.setStyleSheet("color:#c46;")
            self.status_line.setText(error)

    def _on_all_done(self) -> None:
        self.progress.setValue(100)
        if self._downloaded_any and "failed" not in self.status_line.text().lower():
            self.status_line.setText("Done. New maps are available in the list.")
        self.close_button.setEnabled(True)
        self.download_button.setText("Close")
        self.download_button.clicked.disconnect()
        self.download_button.clicked.connect(self.accept)
        self.download_button.setEnabled(True)

    def downloaded_any(self) -> bool:
        return self._downloaded_any

    def closeEvent(self, event) -> None:
        # If a download is mid-flight, cancel it cleanly.
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
        super().closeEvent(event)
