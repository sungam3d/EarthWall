"""
Sets the freshly rendered image as the desktop wallpaper. Detects the
running desktop environment and dispatches to the right mechanism -
each DE has its own API for this, there's no standard.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def detect_desktop() -> str:
    xdg = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    session = os.environ.get("DESKTOP_SESSION", "").lower()
    combined = f"{xdg} {session}"

    if "gnome" in combined or "unity" in combined:
        return "gnome"
    if "kde" in combined or "plasma" in combined:
        return "kde"
    if "cinnamon" in combined:
        return "cinnamon"
    if "xfce" in combined:
        return "xfce"
    if "mate" in combined:
        return "mate"
    if "sway" in combined:
        return "sway"
    return "generic"


def alternating_wallpaper_paths(base: str | Path) -> tuple[Path, Path]:
    """Given a base path like .../current.jpg, return the (A, B) pair
    .../current_a.jpg and .../current_b.jpg used for flicker-free updates."""
    base = Path(base)
    return (base.with_name(f"{base.stem}_a{base.suffix}"),
            base.with_name(f"{base.stem}_b{base.suffix}"))


def pick_next_wallpaper_path(base: str | Path) -> Path:
    """Choose which of the two alternating files to render into next: the
    one NOT currently being displayed (i.e. the older one). Rendering to a
    fresh path and then pointing the desktop at it means the file the DE is
    showing is never touched mid-display (no flash to black), and the URI
    genuinely changes each update, which forces DEs that cache wallpaper
    by URI (GNOME, KDE) to actually load the new image."""
    a, b = alternating_wallpaper_paths(base)
    if not a.exists():
        return a
    if not b.exists():
        return b
    return a if a.stat().st_mtime <= b.stat().st_mtime else b


def _run(cmd: list[str]) -> bool:
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def set_wallpaper(image_path: str | Path, desktop: str | None = None) -> bool:
    image_path = str(Path(image_path).resolve())
    uri = f"file://{image_path}"
    desktop = desktop or detect_desktop()

    if desktop == "gnome":
        ok = _run(["gsettings", "set", "org.gnome.desktop.background",
                   "picture-uri", uri])
        # GNOME 42+ also has a separate dark-mode wallpaper key.
        _run(["gsettings", "set", "org.gnome.desktop.background",
              "picture-uri-dark", uri])
        return ok

    if desktop == "cinnamon":
        return _run(["gsettings", "set", "org.cinnamon.desktop.background",
                     "picture-uri", uri])

    if desktop == "kde":
        # Plasma 6 ships a CLI helper for exactly this. Fall back to the
        # older D-Bus scripting method if it's missing - note the binary
        # is named qdbus6 on some distros (e.g. Arch-based) and qdbus on
        # others, so try both.
        if shutil.which("plasma-apply-wallpaperimage"):
            return _run(["plasma-apply-wallpaperimage", image_path])
        script = f'''
        var allDesktops = desktops();
        for (i = 0; i < allDesktops.length; i++) {{
            d = allDesktops[i];
            d.wallpaperPlugin = "org.kde.image";
            d.currentConfigGroup = ["Wallpaper", "org.kde.image", "General"];
            d.writeConfig("Image", "file://{image_path}");
        }}
        '''
        for qdbus_bin in ("qdbus6", "qdbus", "qdbus-qt6"):
            if shutil.which(qdbus_bin):
                return _run([qdbus_bin, "org.kde.plasmashell", "/PlasmaShell",
                             "org.kde.PlasmaShell.evaluateScript", script])
        return False

    if desktop == "xfce":
        # XFCE stores this per-monitor/workspace property; setting the
        # common "last-image" property covers the typical single-image case.
        list_out = subprocess.run(
            ["xfconf-query", "-c", "xfce4-desktop", "-l"],
            capture_output=True, text=True,
        )
        props = [l for l in list_out.stdout.splitlines() if l.endswith("last-image")]
        if not props:
            return False
        ok = True
        for prop in props:
            ok &= _run(["xfconf-query", "-c", "xfce4-desktop", "-p", prop,
                        "-s", image_path])
        return ok

    if desktop == "mate":
        return _run(["gsettings", "set", "org.mate.background",
                     "picture-filename", image_path])

    if desktop == "sway":
        if shutil.which("swaybg"):
            subprocess.Popen(["pkill", "swaybg"])
            subprocess.Popen(["swaybg", "-i", image_path, "-m", "fill"])
            return True
        return False

    # Generic X11 fallback - works on most lightweight WMs (i3, bspwm, etc).
    if shutil.which("feh"):
        return _run(["feh", "--bg-fill", image_path])

    return False
