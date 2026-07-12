"""Detect whether a fullscreen window (game, video player, presentation)
is currently in focus on any output.

Used to pause the wallpaper auto-update loop when the user is playing a
game so background render work doesn't compete with the GPU/CPU cycles
the game needs. Best-effort: on window managers or Wayland compositors
where we can't determine this cheaply, we just return False (no pause)
rather than blocking the user out of updates.

Implementation:
1. First choice — the standard X11 hint. ``xprop`` on the root window
   for ``_NET_ACTIVE_WINDOW`` gives the active window ID; ``xprop`` on
   that ID for ``_NET_WM_STATE`` reveals ``_NET_WM_STATE_FULLSCREEN``
   when the window is fullscreen (games, mpv, etc). Widely supported.
2. Fallback — if xprop isn't available or errors out, return False.

We deliberately DON'T use polling libraries or add a dependency here;
xprop is packaged in x11-utils on every Ubuntu/Debian/Fedora install
we've seen, and the two subprocess calls only run when the user turns
"Pause on fullscreen" on.
"""
from __future__ import annotations

import shutil
import subprocess

_XPROP_TIMEOUT = 1.0   # generous cap; xprop is normally sub-10ms


def _which_xprop() -> str | None:
    """Cached lookup of the xprop binary path (None if not installed)."""
    global _xprop_path
    try:
        return _xprop_path
    except NameError:
        pass
    _xprop_path = shutil.which("xprop")
    return _xprop_path


def is_fullscreen_window_active() -> bool:
    """Return True iff the currently-focused X11 window has the
    ``_NET_WM_STATE_FULLSCREEN`` state set.

    Never raises: any subprocess error, timeout, or parse failure is
    treated as "unknown" and returns False, so the caller (the auto-
    update timer) simply proceeds with a normal render. That is the
    safe default - the worst outcome of a false negative is a wallpaper
    update while a game is running; the worst outcome of a false
    positive would be the wallpaper never updating."""
    xprop = _which_xprop()
    if xprop is None:
        return False
    try:
        # Step 1: active window id from the root window.
        r = subprocess.run(
            [xprop, "-root", "_NET_ACTIVE_WINDOW"],
            capture_output=True, text=True, timeout=_XPROP_TIMEOUT,
        )
        if r.returncode != 0:
            return False
        # Output looks like: _NET_ACTIVE_WINDOW(WINDOW): window id # 0x1400002
        win_id = None
        for token in r.stdout.split():
            if token.startswith("0x"):
                win_id = token.rstrip(",")
                break
        if not win_id or win_id == "0x0":
            return False

        # Step 2: state atoms on that window.
        r2 = subprocess.run(
            [xprop, "-id", win_id, "_NET_WM_STATE"],
            capture_output=True, text=True, timeout=_XPROP_TIMEOUT,
        )
        if r2.returncode != 0:
            return False
        return "_NET_WM_STATE_FULLSCREEN" in r2.stdout
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError):
        return False
