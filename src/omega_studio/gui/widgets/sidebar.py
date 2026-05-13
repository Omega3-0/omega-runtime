"""Left sidebar navigation for the main window (LM Studio–style)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)


class _NavItem(QFrame):
    """Single clickable sidebar row with icon + label."""

    clicked = Signal()

    def __init__(self, icon: str, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(40)
        self.setProperty("class", "nav-item")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._icon = QLabel(icon)
        self._icon.setProperty("class", "nav-icon")
        self._icon.setFixedWidth(42)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._text = QLabel(label)
        self._text.setProperty("class", "nav-text")

        lay.addWidget(self._icon)
        lay.addWidget(self._text, stretch=1)
        lay.addSpacerItem(QSpacerItem(8, 8, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed))

    def set_selected(self, selected: bool) -> None:
        self.setProperty("class", "nav-item-selected" if selected else "nav-item")
        # Force style refresh
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class Sidebar(QWidget):
    """Vertical sidebar with selectable page buttons."""

    page_changed = Signal(int)

    _PAGES: list[tuple[str, str]] = [
        ("\u25c8", "Dashboard"),
        ("\u25c9", "Models"),
        ("\u2756", "Playground"),
        ("\u25bc", "Downloads"),
        ("\u2699", "Settings"),
        ("\u23da", "Backend"),
        ("\u2726", "Advanced"),
        ("\u25ce", "Server"),
        ("\u25a4", "Logs"),
    ]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(180)
        self._items: list[_NavItem] = []
        self._current = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 12, 0, 12)
        outer.setSpacing(4)

        # App branding
        brand = QLabel("Omega 3.0")
        brand.setStyleSheet("font-size:16px;font-weight:700;color:#3db8c6;padding-left:18px;")
        outer.addWidget(brand)

        sub = QLabel("Runtime Studio")
        sub.setStyleSheet("font-size:11px;font-weight:600;color:#9aa3b8;padding-left:18px;padding-bottom:10px;")
        outer.addWidget(sub)

        divider = QFrame()
        divider.setProperty("class", "divider")
        divider.setFixedHeight(1)
        outer.addWidget(divider)
        outer.addSpacing(8)

        for idx, (icon, label) in enumerate(self._PAGES):
            item = _NavItem(icon, label)
            item.clicked.connect(lambda checked=False, i=idx: self._select(i))
            self._items.append(item)
            outer.addWidget(item)

        outer.addStretch()

        # Footer hint
        hint = QLabel("v3.0")
        hint.setStyleSheet("font-size:11px;color:#9aa3b8;padding-left:18px;")
        outer.addWidget(hint)

        self._apply_selection()

    def _select(self, idx: int) -> None:
        if idx == self._current:
            return
        self._current = idx
        self._apply_selection()
        self.page_changed.emit(idx)

    def _apply_selection(self) -> None:
        for i, item in enumerate(self._items):
            item.set_selected(i == self._current)

    def set_current(self, idx: int) -> None:
        if 0 <= idx < len(self._items) and idx != self._current:
            self._current = idx
            self._apply_selection()

    def current(self) -> int:
        return self._current
