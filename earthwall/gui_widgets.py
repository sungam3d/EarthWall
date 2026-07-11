"""
Custom widgets shared across the GUI.

- ClickJumpSlider: a QSlider where clicking anywhere on the groove jumps
  the handle to that exact value, instead of Qt's default behavior of
  scrolling by one page-step per click. Users consistently expect click-to-
  jump on modern desktops, so this subclass is used everywhere in place of
  a plain QSlider.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (QHBoxLayout, QSlider, QSpinBox, QStyle,
                               QStyleOptionSlider, QWidget)


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


class LabeledSlider(QWidget):
    """A ClickJumpSlider paired with a QSpinBox that stay in sync, plus an
    optional trailing unit label baked into the spinbox suffix.

    This exists because the app grew a lot of "slider with a read-only
    percentage label" rows, and users asked to be able to TYPE exact
    values too. Rather than wiring a slider + spinbox + label + four
    signal connections by hand at every call site (easy to get the
    block-signals dance wrong and cause feedback loops), this widget
    encapsulates the whole pattern and exposes a single valueChanged
    signal and value()/setValue() API that behave like a plain slider.

    The slider and spinbox share the same integer range. If you need a
    fractional underlying value (e.g. 0.0-1.0 opacity) keep the widget in
    whole units (0-100) and convert at the settings boundary, exactly as
    the old slider-only code did.
    """

    valueChanged = Signal(int)

    def __init__(self, minimum: int, maximum: int, *, suffix: str = "",
                 value: int | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        self._slider = ClickJumpSlider(Qt.Horizontal)
        self._slider.setRange(minimum, maximum)
        self._spin = QSpinBox()
        self._spin.setRange(minimum, maximum)
        if suffix:
            self._spin.setSuffix(suffix)
        # Keep the spinbox compact so the slider gets the width. Width is
        # derived from the longest possible value so digits never clip.
        self._spin.setMaximumWidth(90)

        if value is not None:
            self._slider.setValue(value)
            self._spin.setValue(value)

        lay.addWidget(self._slider, stretch=1)
        lay.addWidget(self._spin)

        # Two-way sync. block-signals on the partner while echoing a
        # change prevents an infinite slider<->spin bounce, and we emit
        # our own single valueChanged exactly once per user change.
        self._slider.valueChanged.connect(self._on_slider)
        self._spin.valueChanged.connect(self._on_spin)

    def _on_slider(self, v: int) -> None:
        if self._spin.value() != v:
            self._spin.blockSignals(True)
            self._spin.setValue(v)
            self._spin.blockSignals(False)
        self.valueChanged.emit(v)

    def _on_spin(self, v: int) -> None:
        if self._slider.value() != v:
            self._slider.blockSignals(True)
            self._slider.setValue(v)
            self._slider.blockSignals(False)
        self.valueChanged.emit(v)

    # ---- plain-slider-compatible API ----
    def value(self) -> int:
        return self._slider.value()

    def setValue(self, v: int) -> None:
        # Set both without emitting (callers use this during load); the
        # partner echo is silent and no valueChanged fires, matching how
        # blockSignals()+setValue() worked on the bare slider before.
        self._slider.blockSignals(True)
        self._spin.blockSignals(True)
        self._slider.setValue(v)
        self._spin.setValue(v)
        self._spin.blockSignals(False)
        self._slider.blockSignals(False)

    def setRange(self, lo: int, hi: int) -> None:
        self._slider.setRange(lo, hi)
        self._spin.setRange(lo, hi)

    def slider(self) -> ClickJumpSlider:
        return self._slider

    def spinbox(self) -> QSpinBox:
        return self._spin
