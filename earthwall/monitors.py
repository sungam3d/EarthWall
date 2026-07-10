"""
Multi-monitor detection and virtual-desktop geometry.

Everything Phase 2 (multi-monitor support) needs to know about the user's
displays is derived from here so the rest of the code never has to touch
Qt's QScreen API directly. That means:

- Renderer, wallpaper applier, and preview UI all share one consistent
  view of "what monitors exist and where they sit relative to each
  other" - no surprises where the preview shows one layout and the
  wallpaper composes a different one.
- Falling back gracefully when Qt isn't available (unit tests, CLI use)
  is a single try/except at import time; callers get a sensible
  single-monitor layout instead of a hard crash.

Coordinate system
-----------------
All rectangles use Qt's virtual-desktop coordinates: (x, y, w, h) where
(0, 0) is the top-left of the *primary* monitor, and other monitors sit
at whatever offset the user's OS reports (negative x/y is normal - a
monitor placed to the left of primary has x < 0). We deliberately do NOT
normalise to non-negative coordinates here; the diagonal-layout case
(EarthView-04.jpg) depends on preserving that raw geometry so the void-
fill areas map onto the correct pixels of the final spanned image.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class Monitor:
    """A single physical display."""
    index: int
    name: str
    x: int
    y: int
    width: int
    height: int
    is_primary: bool = False

    @property
    def aspect(self) -> float:
        return self.width / max(1, self.height)

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.width, self.height


@dataclass
class MonitorLayout:
    """The full set of monitors plus the virtual-desktop bounding box.

    `virtual_*` is the smallest axis-aligned rectangle that contains
    every monitor - it's what the spanned wallpaper image must cover.
    For a single monitor this equals that monitor's rect. For a diagonal
    two-monitor setup it's larger than the union of the two rects (the
    'void' between them is what void_fill paints)."""
    monitors: List[Monitor] = field(default_factory=list)
    virtual_x: int = 0
    virtual_y: int = 0
    virtual_width: int = 1920
    virtual_height: int = 1080

    @property
    def count(self) -> int:
        return len(self.monitors)

    @property
    def is_multi(self) -> bool:
        return self.count > 1

    @property
    def virtual_aspect(self) -> float:
        return self.virtual_width / max(1, self.virtual_height)

    def primary(self) -> Monitor:
        for m in self.monitors:
            if m.is_primary:
                return m
        return self.monitors[0] if self.monitors else _fallback_monitor()

    def local_rect(self, m: Monitor) -> tuple[int, int, int, int]:
        """Monitor rect translated so the virtual desktop's top-left is
        (0, 0) - i.e. its position in the composed wallpaper image."""
        return (m.x - self.virtual_x, m.y - self.virtual_y, m.width, m.height)


def _fallback_monitor() -> Monitor:
    return Monitor(0, "primary", 0, 0, 1920, 1080, is_primary=True)


def _fallback_layout() -> MonitorLayout:
    m = _fallback_monitor()
    return MonitorLayout([m], m.x, m.y, m.width, m.height)


def detect_layout() -> MonitorLayout:
    """Enumerate the current display layout. Safe to call even when Qt
    isn't imported or no display is available - returns a sensible single
    1920x1080 primary as a fallback."""
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:
        return _fallback_layout()

    app = QApplication.instance()
    if app is None:
        # Callers running headless (CLI, unit tests) - don't spin up a Qt
        # app just to introspect screens. Return the safe fallback.
        return _fallback_layout()

    screens = app.screens()
    if not screens:
        return _fallback_layout()

    primary = app.primaryScreen()
    monitors: list[Monitor] = []
    for i, s in enumerate(screens):
        g = s.geometry()
        monitors.append(Monitor(
            index=i,
            name=s.name() or f"Display {i + 1}",
            x=g.x(), y=g.y(), width=g.width(), height=g.height(),
            is_primary=(s is primary),
        ))

    # Virtual desktop bounding box - min corner to max corner across ALL
    # monitors. Preserves negative offsets (monitor left/above primary).
    min_x = min(m.x for m in monitors)
    min_y = min(m.y for m in monitors)
    max_x = max(m.x + m.width for m in monitors)
    max_y = max(m.y + m.height for m in monitors)
    return MonitorLayout(
        monitors=monitors,
        virtual_x=min_x, virtual_y=min_y,
        virtual_width=max_x - min_x, virtual_height=max_y - min_y,
    )


# ---------- settings-schema helpers ---------------------------------------
#
# Per-monitor placement settings live under settings["monitor_configs"] as
# a dict keyed by monitor index (as a string, for JSON friendliness). This
# is the data model the placement UI (2.4) reads from and writes back to.

DEFAULT_MONITOR_CONFIG = {
    "zoom": 1.0,        # 1.0 = fit the whole map into the screen
    "offset_x": 0.0,    # -1.0..+1.0, fraction of screen width; +ve = pan right
    "offset_y": 0.0,    # -1.0..+1.0, fraction of screen height; +ve = pan down
    "void_fill_color": "#000000",
    "void_fill_image": None,  # optional path; overrides color when set
}


def monitor_config_for(settings: dict, monitor_index: int) -> dict:
    """Return the placement config for a given monitor, filling in any
    missing keys with defaults. Never mutates `settings`."""
    all_cfgs = settings.get("monitor_configs") or {}
    cfg = dict(DEFAULT_MONITOR_CONFIG)
    cfg.update(all_cfgs.get(str(monitor_index), {}))
    return cfg


def set_monitor_config(settings: dict, monitor_index: int, cfg: dict) -> None:
    """Store a per-monitor config back into settings dict (in-place)."""
    all_cfgs = dict(settings.get("monitor_configs") or {})
    all_cfgs[str(monitor_index)] = cfg
    settings["monitor_configs"] = all_cfgs
