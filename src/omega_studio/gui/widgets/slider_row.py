"""Reusable slider + value label row for model tuning (LM Studio–style)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QWidget


class SliderRow(QWidget):
    """Horizontal row: label | slider | value label.

    *min_val* and *max_val* define the logical range.
    The underlying QSlider always uses an integer range of 0..1000
    and maps linearly back to the logical range.
    """

    value_changed = Signal(float)

    def __init__(
        self,
        label: str,
        min_val: float,
        max_val: float,
        step: float = 1.0,
        decimals: int = 0,
        suffix: str = "",
        special_value_text: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._min = float(min_val)
        self._max = float(max_val)
        if self._max <= self._min:
            self._max = self._min + 1.0
        self._step = float(step)
        if self._step <= 0:
            self._step = 1.0
        self._decimals = max(0, decimals)
        self._suffix = suffix
        self._special = special_value_text

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        self._name = QLabel(label)
        self._name.setMinimumWidth(110)
        lay.addWidget(self._name)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 1000)
        span = self._max - self._min
        slider_step = int(max(1, round(self._step / span * 1000)))
        self._slider.setSingleStep(slider_step)
        self._slider.setPageStep(slider_step * 5)
        self._slider.valueChanged.connect(self._on_slider)
        lay.addWidget(self._slider, stretch=1)

        self._value = QLabel()
        self._value.setMinimumWidth(80)
        self._value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._value)

    # --- internal mapping ---
    def _slider_to_val(self, pos: int) -> float:
        frac = pos / 1000.0
        raw = self._min + frac * (self._max - self._min)
        if self._step > 0:
            steps = round((raw - self._min) / self._step)
            raw = self._min + steps * self._step
        return raw

    def _val_to_slider(self, val: float) -> int:
        if self._max == self._min:
            return 0
        frac = (val - self._min) / (self._max - self._min)
        return max(0, min(1000, int(round(frac * 1000))))

    def _fmt(self, val: float) -> str:
        if self._special is not None and abs(val - self._min) < 1e-9:
            return self._special
        if self._decimals == 0:
            return f"{int(round(val))}{self._suffix}"
        return f"{val:.{self._decimals}f}{self._suffix}"

    def _on_slider(self, pos: int) -> None:
        val = self._slider_to_val(pos)
        self._value.setText(self._fmt(val))
        self.value_changed.emit(val)

    # --- public api ---
    def set_value(self, val: float) -> None:
        val = max(self._min, min(self._max, val))
        self._slider.blockSignals(True)
        self._slider.setValue(self._val_to_slider(val))
        self._slider.blockSignals(False)
        self._value.setText(self._fmt(val))

    def value(self) -> float:
        return self._slider_to_val(self._slider.value())
