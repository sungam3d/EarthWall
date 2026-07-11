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


def _kde_set_fillmode() -> None:
    """Force KDE Plasma's wallpaper FillMode to Stretch (0), so an image
    is shown at exact pixel dimensions. Without this, KDE's default
    "Scale and Crop" would chop out any void bar we deliberately added
    via a map_pos offset. Silent on failure - purely a display polish."""
    script = '''
    var allDesktops = desktops();
    for (i = 0; i < allDesktops.length; i++) {
        d = allDesktops[i];
        d.wallpaperPlugin = "org.kde.image";
        d.currentConfigGroup = ["Wallpaper", "org.kde.image", "General"];
        d.writeConfig("FillMode", 0);
    }
    '''
    for qdbus_bin in ("qdbus6", "qdbus", "qdbus-qt6"):
        if shutil.which(qdbus_bin):
            _run([qdbus_bin, "org.kde.plasmashell", "/PlasmaShell",
                  "org.kde.PlasmaShell.evaluateScript", script])
            return


def set_wallpaper(image_path: str | Path, desktop: str | None = None,
                    spanned: bool = False) -> bool:
    """Apply `image_path` as the desktop wallpaper.

    `spanned=True` asks the DE to stretch a single image across all
    monitors as one continuous surface (rather than the default of
    duplicating it or centring per-monitor). Used by span/independent
    monitor modes so an image sized to the virtual desktop shows the
    right pixels on the right screens. Falls back gracefully when the
    running DE has no equivalent option - the image is applied in the
    DE's default mode and the caller gets a normal `True`.
    """
    image_path = str(Path(image_path).resolve())
    uri = f"file://{image_path}"
    desktop = desktop or detect_desktop()

    if desktop == "gnome":
        ok = _run(["gsettings", "set", "org.gnome.desktop.background",
                   "picture-uri", uri])
        # GNOME 42+ also has a separate dark-mode wallpaper key.
        _run(["gsettings", "set", "org.gnome.desktop.background",
              "picture-uri-dark", uri])
        # We always set picture-options ourselves so the image is
        # displayed 1:1 unscaled. Without this, GNOME's default "zoom"
        # crops the image to fill (chopping off any void bar the user
        # added by shifting the map with map_pos_x/y), or "wallpaper"
        # tiles it, so a deliberate offset would never be visible on the
        # desktop. "spanned" for the multi-monitor case, "stretched"
        # (which shows the image at its exact pixel dimensions when the
        # image matches the screen size, as ours always does) for the
        # single-monitor/mirror case.
        _run(["gsettings", "set", "org.gnome.desktop.background",
              "picture-options", "spanned" if spanned else "stretched"])
        return ok

    if desktop == "cinnamon":
        ok = _run(["gsettings", "set", "org.cinnamon.desktop.background",
                   "picture-uri", uri])
        _run(["gsettings", "set", "org.cinnamon.desktop.background",
              "picture-options", "spanned" if spanned else "stretched"])
        return ok

    if desktop == "kde":
        # Plasma 6 ships a CLI helper for exactly this. Fall back to the
        # older D-Bus scripting method if it's missing - note the binary
        # is named qdbus6 on some distros (e.g. Arch-based) and qdbus on
        # others, so try both.
        if shutil.which("plasma-apply-wallpaperimage"):
            ok = _run(["plasma-apply-wallpaperimage", image_path])
            # As with GNOME below, force stretch/exact-pixel FillMode so
            # any void bar from a map_pos offset is visible instead of
            # being cropped by KDE's default "scale and crop".
            _kde_set_fillmode()
            return ok
        script = f'''
        var allDesktops = desktops();
        for (i = 0; i < allDesktops.length; i++) {{
            d = allDesktops[i];
            d.wallpaperPlugin = "org.kde.image";
            d.currentConfigGroup = ["Wallpaper", "org.kde.image", "General"];
            d.writeConfig("Image", "file://{image_path}");
            d.writeConfig("FillMode", 0);   // 0 = Stretch (1:1 pixel-exact)
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
        # image-style 3 = Stretched (1:1 pixels shown as-is); default 5
        # (Zoomed) would crop out any void bar from a map_pos offset.
        style_props = [p.replace("last-image", "image-style") for p in props]
        for prop in style_props:
            _run(["xfconf-query", "-c", "xfce4-desktop", "-p", prop,
                  "-s", "3"])
        return ok

    if desktop == "mate":
        ok = _run(["gsettings", "set", "org.mate.background",
                   "picture-filename", image_path])
        # Same reason as GNOME above: set picture-options so the image
        # renders 1:1 and any void bar (from a map_pos offset) is shown.
        _run(["gsettings", "set", "org.mate.background",
              "picture-options", "spanned" if spanned else "stretched"])
        return ok

    if desktop == "sway":
        if shutil.which("swaybg"):
            subprocess.Popen(["pkill", "swaybg"])
            # `stretch` displays the image at exact pixel dimensions,
            # preserving any void bar. `fill` and `fit` would scale/crop
            # and lose a deliberate offset. `stretch` is right for both
            # the spanned and single-output cases because our render is
            # already sized to the target surface.
            subprocess.Popen(["swaybg", "-i", image_path, "-m", "stretch"])
            return True
        return False

    # Generic X11 fallback - works on most lightweight WMs (i3, bspwm, etc).
    if shutil.which("feh"):
        # --bg-scale displays the image at the screen's exact size,
        # preserving any void bar we added. --bg-fill would crop it.
        # --no-xinerama treats all outputs as one canvas for spanned mode.
        cmd = ["feh"]
        if spanned:
            cmd.append("--no-xinerama")
        cmd += ["--bg-scale", image_path]
        return _run(cmd)

    return False
