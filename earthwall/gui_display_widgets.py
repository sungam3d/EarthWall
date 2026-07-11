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
        coords into widget coords.

        Critically, this fits ONLY the virtual desktop (the monitor
        rectangles) into the widget - never the map area. That means the
        monitor rectangle is always drawn at the same size and always
        fully visible, no matter how far the map is zoomed in or out. The
        map rectangle is then drawn relative to that same transform and
        simply clipped to the widget if it spills outside (zoom > 100%),
        so the desktop appears to sit *behind* the map exactly as it
        does on the real screen. Reserves a generous margin so a zoomed-
        out map (which sits inside the monitor) still has room, and a
        zoomed-in map's overflow has somewhere to bleed to."""
        margin = 24
        avail_w = max(1, self.width() - margin * 2)
        avail_h = max(1, self.height() - margin * 2)
        if self._layout is None or self._layout.virtual_width <= 0:
            return 1.0, margin, margin
        sx = avail_w / self._layout.virtual_width
        sy = avail_h / self._layout.virtual_height
        s = min(sx, sy)
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
        p.fillRect(self.rect(), _VIRTUAL_BG)

        if self._layout is None or not self._layout.monitors:
            p.setPen(QColor(150, 150, 150))
            p.drawText(self.rect(), Qt.AlignCenter, "No displays detected")
            return

        s, ox, oy = self._fit_transform()

        def to_widget(vx, vy, vw, vh):
            return QRectF(vx * s + ox, vy * s + oy, vw * s, vh * s)

        # Map-area rect in virtual-desktop coords -> widget coords. This
        # is the FULL map image at (virtual_desktop * zoom); the red box
        # always outlines the whole map and the thumbnail always fills it.
        area = self._map_area
        if area is None:
            area = (self._layout.virtual_x, self._layout.virtual_y,
                    self._layout.virtual_width, self._layout.virtual_height)
        map_rect = to_widget(*area)

        # Monitor rectangles in widget coords - these are the "windows"
        # (masks) through which the map is seen at full brightness.
        mon_rects = [to_widget(m.x, m.y, m.width, m.height)
                     for m in self._layout.monitors]

        # ---- Layer 1: the map OUTSIDE the screens, dimmed --------------
        # Draw the whole map thumbnail (filling the entire red box) at
        # reduced opacity across the widget, so wherever the map extends
        # beyond the monitors you can still see it faintly. This is the
        # "map behind the desktop" layer.
        p.save()
        p.setClipRect(self.rect())
        if self._map_thumb is not None and not self._map_thumb.isNull():
            p.setOpacity(0.35)
            p.drawPixmap(map_rect.toRect(), self._map_thumb)
            p.setOpacity(1.0)
        p.restore()

        # ---- Layer 2: the monitors (the mask) -------------------------
        # Fill each monitor with the screen colour first (this is what
        # shows through as "screen" wherever the map doesn't cover it,
        # e.g. zoom < 100%), then punch the map back in at full
        # brightness clipped to the monitor - so inside the screen you
        # see the map crisply, exactly as it'll appear on the desktop.
        for m, r in zip(self._layout.monitors, mon_rects):
            fill = _MONITOR_FILL_PRIMARY if m.is_primary else _MONITOR_FILL
            p.setPen(QPen(_MONITOR_EDGE, 1))
            p.setBrush(QBrush(fill))
            p.drawRoundedRect(r, 3, 3)

        if self._map_thumb is not None and not self._map_thumb.isNull():
            for r in mon_rects:
                p.save()
                p.setClipRect(r)
                p.drawPixmap(map_rect.toRect(), self._map_thumb)
                p.restore()

        # Monitor number badges on top of the map slice.
        for m, r in zip(self._layout.monitors, mon_rects):
            self._draw_badge(p, int(r.x() + 4), int(r.y() + 4),
                             str(m.index + 1), QColor(90, 90, 100))

        # ---- Layer 3: the red outline of the full map -----------------
        # Always drawn last so the whole map's extent is visible even
        # where it spills past the monitors (zoom > 100%). Clipped to the
        # widget so a huge zoomed-in map's outline doesn't draw miles off
        # into negative space, but the edges that fall within the widget
        # still show.
        p.save()
        p.setClipRect(self.rect())
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(_RED, 2))
        p.drawRect(map_rect)
        p.restore()
        badge_x = int(max(self.rect().left() + 4,
                          min(map_rect.x() + 4, self.rect().right() - 24)))
        badge_y = int(max(self.rect().top() + 4,
                          min(map_rect.y() + 4, self.rect().bottom() - 20)))
        self._draw_badge(p, badge_x, badge_y, "1", _BADGE_BG)

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
