"""
Custom widgets for the multi-monitor "Displays" tab.

Design mirrors EarthView's wallpaper editor:

- ScreenAreaPreview: shows the virtual desktop as a dark area, individual
  monitors as brighter rectangles inside it, and the wallpaper's "map
  area" as a red-outlined rectangle overlaid on top. Handles diagonal
  layouts by drawing each monitor at its true virtual-desktop position
  (see monitors.MonitorLayout for the coordinate system).

- MapFocalPointPreview: a small 2:1 world map with a draggable red dot
  marking the current center longitude/latitude. Dragging emits a signal
  with the new (lon, lat); the tab wires that back to the settings model
  so the main preview updates immediately.

Both widgets deliberately paint at whatever size the layout gives them -
they compute a uniform scale factor to fit the virtual desktop (or the
2:1 map) into their available area, so they look correct on a 4K display
and on a laptop screen without any manual sizing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QRect, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from .monitors import MonitorLayout


# The red used to outline the map area and mark the focal point. Matches
# EarthView's palette closely enough to feel familiar to users switching
# between the two tools.
_RED = QColor(220, 40, 40)
_MONITOR_FILL = QColor(60, 60, 66)
_MONITOR_FILL_PRIMARY = QColor(75, 75, 82)
_MONITOR_EDGE = QColor(120, 120, 130)
_VIRTUAL_BG = QColor(28, 28, 32)
_BADGE_BG = QColor(220, 40, 40)


class ScreenAreaPreview(QWidget):
    """Read-only visualisation of the current monitor layout with the
    wallpaper's map area outlined on top.

    The widget scales the virtual desktop uniformly to fit the widget's
    interior, preserving aspect ratio and monitor positions. Diagonal /
    offset layouts (EarthView-04.jpg case) draw correctly because each
    monitor's rect is placed at its true virtual-desktop coordinates,
    not stacked left-to-right.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._layout: MonitorLayout | None = None
        # Map area in virtual-desktop coordinates: (x, y, w, h). None =
        # "auto" (map covers full virtual desktop).
        self._map_area: tuple[int, int, int, int] | None = None
        # Optional thumbnail (usually the last low-res preview render) to
        # paint inside the map area, giving a proper "here's what it will
        # look like on your desktop" impression.
        self._map_thumb: QPixmap | None = None
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    # ----- public API --------------------------------------------------
    def set_layout(self, layout: MonitorLayout) -> None:
        self._layout = layout
        self.update()

    def set_map_area(self, area: tuple[int, int, int, int] | None) -> None:
        self._map_area = area
        self.update()

    def set_map_thumbnail(self, pixmap: QPixmap | None) -> None:
        self._map_thumb = pixmap
        self.update()

    # ----- painting ----------------------------------------------------
    def _fit_transform(self) -> tuple[float, float, float]:
        """Return (scale, offset_x, offset_y) mapping virtual-desktop
        coords into widget coords, with a small internal margin so the
        red outline isn't clipped by the widget edge."""
        margin = 10
        avail_w = max(1, self.width() - margin * 2)
        avail_h = max(1, self.height() - margin * 2)
        if self._layout is None or self._layout.virtual_width <= 0:
            return 1.0, margin, margin
        sx = avail_w / self._layout.virtual_width
        sy = avail_h / self._layout.virtual_height
        s = min(sx, sy)
        # Centre the scaled desktop inside the widget.
        used_w = self._layout.virtual_width * s
        used_h = self._layout.virtual_height * s
        ox = (self.width() - used_w) / 2 - self._layout.virtual_x * s
        oy = (self.height() - used_h) / 2 - self._layout.virtual_y * s
        return s, ox, oy

    def paintEvent(self, _event) -> None:
        # A raised exception inside a Qt paintEvent can hard-crash the
        # process on some platforms (notably Windows), with no traceback.
        # Guard the whole thing: worst case we skip a frame, and the
        # error is logged by the global excepthook via the re-raise-free
        # path below.
        try:
            self._paint(_event)
        except Exception:
            import logging
            logging.exception("ScreenAreaPreview paint failed")

    def _paint(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        # Widget background: solid dark, slightly rounded like EarthView.
        p.fillRect(self.rect(), _VIRTUAL_BG)

        if self._layout is None or not self._layout.monitors:
            p.setPen(QColor(150, 150, 150))
            p.drawText(self.rect(), Qt.AlignCenter, "No displays detected")
            return

        s, ox, oy = self._fit_transform()

        # 1. Paint each monitor as a filled rounded rect at its true
        #    virtual-desktop position - diagonal layouts land correctly
        #    because we translate by (virtual_x, virtual_y) in the offset.
        for m in self._layout.monitors:
            rx = m.x * s + ox
            ry = m.y * s + oy
            rw = m.width * s
            rh = m.height * s
            fill = _MONITOR_FILL_PRIMARY if m.is_primary else _MONITOR_FILL
            p.setPen(QPen(_MONITOR_EDGE, 1))
            p.setBrush(QBrush(fill))
            p.drawRoundedRect(QRectF(rx, ry, rw, rh), 3, 3)
            # Monitor number badge (top-left corner).
            self._draw_badge(p, int(rx + 4), int(ry + 4), str(m.index + 1),
                             QColor(90, 90, 100))

        # 2. Paint the map thumbnail (if any) inside the map-area rect,
        #    then draw the red outline over the top and a "1" view badge
        #    in the corner - the visual signature EarthView uses.
        area = self._map_area
        if area is None:
            # Default: whole virtual desktop
            area = (self._layout.virtual_x, self._layout.virtual_y,
                    self._layout.virtual_width, self._layout.virtual_height)
        ax, ay, aw, ah = area
        rx = ax * s + ox
        ry = ay * s + oy
        rw = aw * s
        rh = ah * s
        map_rect = QRectF(rx, ry, rw, rh)

        if self._map_thumb is not None and not self._map_thumb.isNull():
            # Draw the thumbnail scaled to fill the map area (aspect not
            # preserved - the real wallpaper stretches to fill too).
            p.drawPixmap(map_rect.toRect(), self._map_thumb)

        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(_RED, 2))
        p.drawRect(map_rect)
        self._draw_badge(p, int(rx + 4), int(ry + 4), "1", _BADGE_BG)

    def _draw_badge(self, p: QPainter, x: int, y: int, text: str,
                    bg: QColor) -> None:
        f = QFont(self.font())
        f.setBold(True)
        f.setPointSize(max(7, self.font().pointSize()))
        p.setFont(f)
        fm = p.fontMetrics()
        pad_x, pad_y = 5, 1
        w = fm.horizontalAdvance(text) + pad_x * 2
        h = fm.height() + pad_y * 2
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(QRect(x, y, w, h), 2, 2)
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRect(x, y, w, h), Qt.AlignCenter, text)


class MapFocalPointPreview(QWidget):
    """Small equirectangular world map with a draggable red dot marking
    the map center (longitude, latitude). Emits `focal_changed(lon, lat)`
    on drag, throttled to distinct pixel positions.

    The widget always renders at 2:1 aspect (matching the map itself) so
    the dot's screen position translates back to longitude/latitude with
    no distortion. The base map thumbnail is loaded from the same source
    the renderer uses - fall back to a plain blue rect if it isn't
    available (shouldn't happen in a normal install).
    """

    focal_changed = Signal(float, float)  # (lon, lat)

    def __init__(self, parent: QWidget | None = None,
                 map_path: str | Path | None = None):
        super().__init__(parent)
        self._lon = 0.0
        self._lat = 0.0
        self._dragging = False
        self._pixmap: QPixmap | None = None
        if map_path is not None:
            pm = QPixmap(str(map_path))
            if not pm.isNull():
                self._pixmap = pm
        self.setMinimumSize(200, 100)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self):
        # Keep the widget in a 2:1 shape based on whatever width it gets.
        w = self.width() if self.width() > 0 else 260
        return self._sized(w)

    def heightForWidth(self, w: int) -> int:
        return w // 2

    def hasHeightForWidth(self) -> bool:
        return True

    def _sized(self, w: int):
        from PySide6.QtCore import QSize
        return QSize(w, max(100, w // 2))

    # ----- public API --------------------------------------------------
    def set_focal(self, lon: float, lat: float) -> None:
        self._lon = _clamp(lon, -180.0, 180.0)
        self._lat = _clamp(lat, -90.0, 90.0)
        self.update()

    def focal(self) -> tuple[float, float]:
        return self._lon, self._lat

    # ----- geometry helpers -------------------------------------------
    def _map_rect(self) -> QRectF:
        """Rect the map is painted into: 2:1 aspect, centred, small margin."""
        margin = 6
        avail_w = max(1, self.width() - margin * 2)
        avail_h = max(1, self.height() - margin * 2)
        # 2:1 - find whichever dimension is limiting.
        if avail_w / 2 <= avail_h:
            w = avail_w
            h = w // 2
        else:
            h = avail_h
            w = h * 2
        x = (self.width() - w) / 2
        y = (self.height() - h) / 2
        return QRectF(x, y, w, h)

    def _lonlat_to_pt(self, rect: QRectF) -> tuple[float, float]:
        x = rect.x() + (self._lon + 180) / 360 * rect.width()
        y = rect.y() + (90 - self._lat) / 180 * rect.height()
        return x, y

    def _pt_to_lonlat(self, x: float, y: float, rect: QRectF) -> tuple[float, float]:
        fx = (x - rect.x()) / max(1.0, rect.width())
        fy = (y - rect.y()) / max(1.0, rect.height())
        lon = fx * 360 - 180
        lat = 90 - fy * 180
        return _clamp(lon, -180.0, 180.0), _clamp(lat, -90.0, 90.0)

    # ----- painting ----------------------------------------------------
    def paintEvent(self, _event) -> None:
        try:
            self._paint(_event)
        except Exception:
            import logging
            logging.exception("MapFocalPointPreview paint failed")

    def _paint(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), _VIRTUAL_BG)

        rect = self._map_rect()

        if self._pixmap is not None:
            p.drawPixmap(rect.toRect(), self._pixmap)
        else:
            p.fillRect(rect, QColor(20, 40, 90))
        p.setPen(QPen(QColor(100, 100, 110), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(rect)

        # The focal dot: filled red circle with a thin dark outline so
        # it stays visible over bright ocean and dark landmass alike.
        dx, dy = self._lonlat_to_pt(rect)
        r = 6
        p.setPen(QPen(QColor(0, 0, 0), 1))
        p.setBrush(_RED)
        p.drawEllipse(QRectF(dx - r, dy - r, r * 2, r * 2))

    # ----- mouse -------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._update_from_mouse(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            self._update_from_mouse(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._dragging = False

    def _update_from_mouse(self, event: QMouseEvent) -> None:
        pos = event.position() if hasattr(event, "position") else event.pos()
        lon, lat = self._pt_to_lonlat(pos.x(), pos.y(), self._map_rect())
        if (round(lon, 3), round(lat, 3)) == (round(self._lon, 3), round(self._lat, 3)):
            return
        self._lon, self._lat = lon, lat
        self.update()
        self.focal_changed.emit(lon, lat)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))
