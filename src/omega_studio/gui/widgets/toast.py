"""Non-intrusive toast notifications that auto-dismiss (no modal dialogs)."""

from __future__ import annotations

from PySide6.QtCore import QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QGraphicsOpacityEffect, QHBoxLayout, QLabel, QWidget


class ToastCard(QWidget):
    """Single toast bubble."""

    _COLORS: dict[str, str] = {
        "info": "#3db8c6",
        "success": "#4ade80",
        "warning": "#fbbf24",
        "error": "#f87171",
    }

    def __init__(
        self,
        message: str,
        level: str = "info",
        duration_ms: int = 3500,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self._duration = duration_ms
        self._bg = QColor(self._COLORS.get(level, "#3db8c6"))

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        self._lbl = QLabel(message)
        self._lbl.setStyleSheet("color:#0d1118;font-weight:600;font-size:13px;padding:2px;")
        self._lbl.setWordWrap(True)
        lay.addWidget(self._lbl)

        self.setMinimumWidth(220)
        self.setMaximumWidth(420)
        self.adjustSize()

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        # Fade in
        self._anim_in = QPropertyAnimation(self._opacity, b"opacity")
        self._anim_in.setDuration(250)
        self._anim_in.setStartValue(0.0)
        self._anim_in.setEndValue(1.0)
        self._anim_in.start()

        # Auto-dismiss
        QTimer.singleShot(self._duration, self._start_fade_out)

    def _start_fade_out(self) -> None:
        self._anim_out = QPropertyAnimation(self._opacity, b"opacity")
        self._anim_out.setDuration(400)
        self._anim_out.setStartValue(1.0)
        self._anim_out.setEndValue(0.0)
        self._anim_out.finished.connect(self.close)
        self._anim_out.finished.connect(self._anim_out.deleteLater)
        self._anim_out.start()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        rect = self.rect().adjusted(1, 1, -1, -1)
        path.addRoundedRect(rect, 10, 10)
        painter.fillPath(path, self._bg)
        painter.end()


class ToastManager:
    """Simple helper attached to a parent widget."""

    def __init__(self, parent: QWidget) -> None:
        self._parent = parent

    def show(self, message: str, level: str = "info", duration_ms: int = 3500) -> None:
        toast = ToastCard(message, level, duration_ms)
        # Position at bottom-right of parent in screen coordinates
        parent_geo = self._parent.frameGeometry()
        margin = 20
        x = parent_geo.right() - toast.width() - margin
        y = parent_geo.bottom() - toast.height() - margin - 40
        toast.move(x, y)
        toast.show()
