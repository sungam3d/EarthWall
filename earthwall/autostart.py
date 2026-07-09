"""
Autostart-at-login support via the XDG autostart standard - a .desktop
file dropped in ~/.config/autostart/ is picked up by essentially every
Linux desktop environment (GNOME, KDE, XFCE, Cinnamon, MATE, etc.),
without needing systemd or DE-specific configuration.
"""
from __future__ import annotations

import sys
from pathlib import Path

AUTOSTART_DIR = Path.home() / ".config" / "autostart"
DESKTOP_FILE = AUTOSTART_DIR / "earthwall-gui.desktop"


def _launch_command() -> str:
    """Best-effort command to relaunch the GUI, using the same Python
    interpreter (and therefore the same venv) that's running right now."""
    python = sys.executable
    module_dir = Path(__file__).resolve().parent.parent
    return f'{python} -m earthwall.gui'


def is_enabled() -> bool:
    return DESKTOP_FILE.exists()


def enable() -> None:
    AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
    module_dir = Path(__file__).resolve().parent.parent
    content = f"""[Desktop Entry]
Type=Application
Name=Earthwall
Comment=Live real-time Earth wallpaper
Exec=env PYTHONPATH={module_dir} {_launch_command()}
Path={module_dir}
Icon={module_dir / "assets" / "icon.png"}
X-GNOME-Autostart-enabled=true
NoDisplay=false
Terminal=false
"""
    DESKTOP_FILE.write_text(content)


def disable() -> None:
    if DESKTOP_FILE.exists():
        DESKTOP_FILE.unlink()


APPLICATIONS_DIR = Path.home() / ".local" / "share" / "applications"
APPLICATIONS_FILE = APPLICATIONS_DIR / "earthwall-gui.desktop"


def register_in_app_menu() -> None:
    """Adds Earthwall to the normal application launcher/menu (separate
    from the autostart-at-login setting). Safe to call every launch -
    it just overwrites the same file each time."""
    APPLICATIONS_DIR.mkdir(parents=True, exist_ok=True)
    module_dir = Path(__file__).resolve().parent.parent
    content = f"""[Desktop Entry]
Type=Application
Name=Earthwall
Comment=Live real-time Earth wallpaper
Exec=env PYTHONPATH={module_dir} {_launch_command()}
Path={module_dir}
Icon={module_dir / "assets" / "icon.png"}
Categories=Utility;
Terminal=false
"""
    APPLICATIONS_FILE.write_text(content)
