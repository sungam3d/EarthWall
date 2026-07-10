"""
Custom widgets shared across the GUI.

- ClickJumpSlider: a QSlider where clicking anywhere on the groove jumps
  the handle to that exact value, instead of Qt's default behavior of
  scrolling by one page-step per click. Users consistently expect click-to-
  jump on modern desktops, so this subclass is used everywhere in place of
  a plain QSlider.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider


class ClickJumpSlider(QSlider):
    """A horizontal/vertical slider where a left-click on the groove sets
    the value to that position immediately, then continues to drag if the
    button is held.

    Qt's default: click = page step (jump by increment set by pageStep()).
    What almost every user expects: click = jump to clicked position.

    Implementation: on left-press, compute which value the click position
    corresponds to using QStyle's slider geometry helpers, apply it, then
    fall through to the base class so drag-continuation still works.
    """

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return

        # Only jump if the click is NOT on the handle itself (dragging
        # the handle should behave normally). We ask Qt's style for the
        # handle rect and check whether the click lands inside it.
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        handle_rect = self.style().subControlRect(
            QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, self)
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()

        if handle_rect.contains(pos):
            super().mousePressEvent(event)
            return

        # Compute the value the click position corresponds to.
        groove_rect = self.style().subControlRect(
            QStyle.CC_Slider, opt, QStyle.SC_SliderGroove, self)

        if self.orientation() == Qt.Horizontal:
            slider_length = handle_rect.width()
            slider_min = groove_rect.x()
            slider_max = groove_rect.right() - slider_length + 1
            click_pos = pos.x() - slider_length // 2
        else:
            slider_length = handle_rect.height()
            slider_min = groove_rect.y()
            slider_max = groove_rect.bottom() - slider_length + 1
            click_pos = pos.y() - slider_length // 2

        new_value = QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(),
            click_pos - slider_min, slider_max - slider_min,
            opt.upsideDown,
        )
        self.setValue(new_value)

        # Synthesize a fresh press event so the base class treats this as
        # if the user grabbed the handle in its new position - drag then
        # continues naturally without jumping back.
        self.setSliderDown(True)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self.setSliderDown(False)
        super().mouseReleaseEvent(event)
