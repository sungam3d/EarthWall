"""
Entry point for `python -m earthwall.gui`. Runs the app resident in the
system tray - closing the settings window just hides it, so the live
wallpaper updates keep running in the background. Use "Quit" from the
tray menu to actually exit.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import autostart
from .gui_main_window import MainWindow

ICON_PATH = Path(__file__).resolve().parent.parent / "assets" / "icon.png"
# Tray gets its own transparent variant when available - the desktop's tray
# often draws on tinted backgrounds where the rounded-square variant's
# opaque edges look boxed-in. Fall back to the main icon if the transparent
# one isn't shipped.
TRAY_ICON_PATH = Path(__file__).resolve().parent.parent / "assets" / "icon-tray.png"
if not TRAY_ICON_PATH.exists():
    TRAY_ICON_PATH = ICON_PATH


def main() -> None:
    try:
        autostart.register_in_app_menu()
    except Exception:
        pass  # non-critical - worst case it's just missing from the app menu

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("EarthWall")

    icon = QIcon(str(ICON_PATH)) if ICON_PATH.exists() else app.style().standardIcon(
        app.style().StandardPixmap.SP_ComputerIcon
    )
    tray_icon = QIcon(str(TRAY_ICON_PATH)) if TRAY_ICON_PATH.exists() else icon

    window = MainWindow()
    window.setWindowIcon(icon)

    tray = QSystemTrayIcon(tray_icon)
    tray.setToolTip("EarthWall - live Earth wallpaper")

    menu = QMenu()
    open_action = menu.addAction("Open Settings")
    open_action.triggered.connect(lambda: (window.show(), window.raise_(), window.activateWindow()))

    update_action = menu.addAction("Update Now")
    update_action.triggered.connect(window.trigger_update)

    pause_action = menu.addAction("Pause Auto-Update")
    pause_action.setCheckable(True)
    pause_action.setChecked(window.settings.get("paused", False))

    def _toggle_pause(checked: bool) -> None:
        window.pause_btn.setChecked(checked)

    pause_action.toggled.connect(_toggle_pause)
    window.pause_btn.toggled.connect(pause_action.setChecked)

    menu.addSeparator()
    quit_action = menu.addAction("Quit EarthWall")
    quit_action.triggered.connect(app.quit)

    tray.setContextMenu(menu)
    app.aboutToQuit.connect(window.shutdown)

    def _on_tray_activated(reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if window.isVisible():
                window.hide()
            else:
                window.show()
                window.raise_()
                window.activateWindow()

    tray.activated.connect(_on_tray_activated)
    tray.show()

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
