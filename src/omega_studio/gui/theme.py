"""Omega Runtime Studio Qt Fusion palette + stylesheet."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# Deep slate workspace with restrained teal accents (readable, not generic "gray box").
_BG = "#15191f"
_SURFACE = "#1b1f28"
_SURFACE_ELEVATED = "#232833"
_SURFACE_HOVER = "#2a3140"
_BORDER = "#3d4656"
_BORDER_SUBTLE = "#2d3544"
_TEXT = "#e8eaf0"
_TEXT_MUTED = "#9aa3b8"
_ACCENT = "#3db8c6"
_ACCENT_DIM = "#2a8f9c"
_SELECTION = "#2d4a52"
_STATUS_GREEN = "#4ade80"
_STATUS_AMBER = "#fbbf24"
_STATUS_RED = "#f87171"


def apply_studio_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(_BG))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(_TEXT))
    pal.setColor(QPalette.ColorRole.Base, QColor(_SURFACE))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(_SURFACE_ELEVATED))
    pal.setColor(QPalette.ColorRole.Text, QColor(_TEXT))
    pal.setColor(QPalette.ColorRole.Button, QColor(_SURFACE_ELEVATED))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(_TEXT))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(_SELECTION))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(_TEXT))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(_SURFACE_ELEVATED))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(_TEXT))
    app.setPalette(pal)

    f = app.font()
    f.setPointSize(max(10, f.pointSize()))
    app.setFont(f)

    app.setStyleSheet(
        f"""
        QMainWindow {{
            background: {_BG};
        }}
        /* Sidebar navigation buttons */
        QFrame[class="nav-item"] {{
            background: transparent;
            border-left: 3px solid transparent;
            border-radius: 0px;
        }}
        QFrame[class="nav-item"]:hover {{
            background: {_SURFACE_HOVER};
        }}
        QFrame[class="nav-item-selected"] {{
            background: {_SURFACE_ELEVATED};
            border-left: 3px solid {_ACCENT};
        }}
        QFrame[class="nav-item-selected"]:hover {{
            background: {_SURFACE_HOVER};
        }}
        QLabel[class="nav-icon"] {{
            color: {_TEXT_MUTED};
            font-size: 16px;
            font-weight: 700;
            padding-left: 14px;
            background: transparent;
        }}
        QFrame[class="nav-item-selected"] QLabel[class="nav-icon"] {{
            color: {_ACCENT};
        }}
        QLabel[class="nav-text"] {{
            color: {_TEXT_MUTED};
            font-size: 13px;
            font-weight: 600;
            padding-left: 8px;
            background: transparent;
        }}
        QFrame[class="nav-item-selected"] QLabel[class="nav-text"] {{
            color: {_TEXT};
        }}
        /* Header bar */
        QFrame[class="header-bar"] {{
            background: {_SURFACE_ELEVATED};
            border-bottom: 1px solid {_BORDER};
        }}
        QLabel[class="header-title"] {{
            color: {_TEXT};
            font-size: 14px;
            font-weight: 700;
            padding-left: 16px;
            background: transparent;
        }}
        QLabel[class="header-status"] {{
            color: {_TEXT_MUTED};
            font-size: 12px;
            padding-right: 16px;
            background: transparent;
        }}
        /* Tab-like pane (used inside stacked pages) */
        QTabWidget::pane {{
            border: 1px solid {_BORDER};
            border-radius: 6px;
            top: -1px;
            background: {_SURFACE};
        }}
        QTabBar::tab {{
            background: {_SURFACE_ELEVATED};
            color: {_TEXT_MUTED};
            padding: 8px 16px;
            margin-right: 2px;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
        }}
        QTabBar::tab:selected {{
            background: {_SURFACE};
            color: {_TEXT};
            font-weight: 600;
        }}
        QGroupBox {{
            font-weight: 600;
            color: {_TEXT};
            border: 1px solid {_BORDER};
            border-radius: 8px;
            margin-top: 14px;
            padding: 14px 12px 10px 12px;
            background: {_SURFACE_ELEVATED};
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 6px;
            color: {_ACCENT};
        }}
        QPushButton {{
            background: {_ACCENT_DIM};
            color: #0d1118;
            border: none;
            border-radius: 6px;
            padding: 8px 14px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background: {_ACCENT};
        }}
        QPushButton:pressed {{
            background: #4dc8d8;
        }}
        QPushButton:disabled {{
            background: {_BORDER};
            color: {_TEXT_MUTED};
        }}
        QPushButton[class="tool"] {{
            background: {_SURFACE_HOVER};
            color: {_TEXT};
            border: 1px solid {_BORDER};
            padding: 6px 12px;
            font-weight: 600;
        }}
        QPushButton[class="tool"]:hover {{
            background: {_BORDER};
            border: 1px solid {_ACCENT_DIM};
        }}
        QPushButton[class="danger"] {{
            background: #7f1d1d;
            color: {_TEXT};
        }}
        QPushButton[class="danger"]:hover {{
            background: #b91c1c;
        }}
        QPushButton[class="preset"] {{
            background: {_SURFACE_HOVER};
            color: {_TEXT_MUTED};
            border: 1px solid {_BORDER_SUBTLE};
            padding: 4px 10px;
            font-weight: 600;
            font-size: 11px;
        }}
        QPushButton[class="preset"]:hover {{
            background: {_BORDER};
            color: {_TEXT};
            border: 1px solid {_ACCENT_DIM};
        }}
        QPushButton[class="preset-active"] {{
            background: {_ACCENT_DIM};
            color: #0d1118;
            border: 1px solid {_ACCENT};
            padding: 4px 10px;
            font-weight: 600;
            font-size: 11px;
        }}
        QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
            background: {_SURFACE};
            color: {_TEXT};
            border: 1px solid {_BORDER};
            border-radius: 5px;
            padding: 5px 8px;
            min-height: 1.2em;
        }}
        QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
        QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
            border: 1px solid {_ACCENT_DIM};
        }}
        QTableWidget {{
            background: {_BG};
            alternate-background-color: {_SURFACE};
            gridline-color: {_BORDER_SUBTLE};
            border: 1px solid {_BORDER};
            border-radius: 6px;
        }}
        QHeaderView::section {{
            background: {_SURFACE_ELEVATED};
            color: {_TEXT_MUTED};
            padding: 6px;
            border: none;
            border-bottom: 2px solid {_ACCENT_DIM};
            font-weight: 600;
        }}
        QProgressBar {{
            border: 1px solid {_BORDER};
            border-radius: 6px;
            text-align: center;
            height: 20px;
            background: {_SURFACE};
        }}
        QProgressBar::chunk {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 {_ACCENT_DIM}, stop:1 {_ACCENT});
            border-radius: 5px;
        }}
        QScrollArea {{
            border: none;
        }}
        QComboBox QAbstractItemView {{
            background: {_SURFACE_ELEVATED};
            color: {_TEXT};
            outline: none;
            border: 1px solid {_BORDER};
            border-radius: 4px;
        }}
        QComboBox QAbstractItemView::item {{
            padding: 4px 8px;
            min-height: 1.35em;
        }}
        QComboBox QAbstractItemView::item:hover {{
            background: {_SELECTION};
        }}
        QComboBox QAbstractItemView::item:selected {{
            background: {_SELECTION};
        }}
        QStatusBar {{
            background: {_SURFACE};
            color: {_TEXT_MUTED};
            border-top: 1px solid {_BORDER};
            padding: 4px 10px;
            min-height: 1.35em;
        }}
        QStatusBar::item {{
            border: none;
        }}
        QToolBar {{
            background: {_SURFACE_ELEVATED};
            border: none;
            border-bottom: 1px solid {_BORDER};
            spacing: 6px;
            padding: 6px 10px;
        }}
        QToolBar::separator {{
            background: {_BORDER};
            width: 1px;
            margin: 4px 6px;
        }}
        QToolButton {{
            background: {_ACCENT_DIM};
            color: #0d1118;
            border: none;
            border-radius: 5px;
            padding: 6px 12px;
            font-weight: 600;
            margin-right: 2px;
        }}
        QToolButton:hover {{
            background: {_ACCENT};
        }}
        QToolButton:pressed {{
            background: #4dc8d8;
        }}
        QLabel[class="muted"] {{
            color: {_TEXT_MUTED};
        }}
        QLabel[class="kicker"] {{
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.1em;
            color: {_TEXT_MUTED};
        }}
        QLabel[class="title"] {{
            color: {_ACCENT};
            font-size: 18px;
            font-weight: 700;
        }}
        QLabel[class="status-dot"] {{
            font-size: 18px;
            font-weight: 700;
        }}
        QFrame[class="card"] {{
            background: {_SURFACE_ELEVATED};
            border: 1px solid {_BORDER};
            border-left: 4px solid {_ACCENT};
            border-radius: 10px;
        }}
        QFrame[class="card"] > QLabel {{
            background: transparent;
        }}
        QFrame[class="card-subtle"] {{
            background: {_SURFACE_ELEVATED};
            border: 1px solid {_BORDER_SUBTLE};
            border-radius: 8px;
        }}
        QFrame[class="divider"] {{
            background: {_BORDER_SUBTLE};
            max-height: 1px;
            min-height: 1px;
        }}
        QSplitter::handle {{
            background: {_BORDER_SUBTLE};
        }}
        QSplitter::handle:horizontal {{
            width: 2px;
        }}
        QTextEdit[class="chat-history"] {{
            background: {_BG};
            border: 1px solid {_BORDER};
            border-radius: 8px;
        }}
        /* Sliders */
        QSlider::groove:horizontal {{
            height: 6px;
            background: {_BORDER_SUBTLE};
            border-radius: 3px;
        }}
        QSlider::sub-page:horizontal {{
            background: {_ACCENT_DIM};
            border-radius: 3px;
        }}
        QSlider::add-page:horizontal {{
            background: {_BORDER_SUBTLE};
            border-radius: 3px;
        }}
        QSlider::handle:horizontal {{
            background: {_ACCENT};
            width: 14px;
            height: 14px;
            margin: -4px 0;
            border-radius: 7px;
        }}
        QSlider::handle:horizontal:hover {{
            background: #4dc8d8;
        }}
        /* Log viewer */
        QPlainTextEdit[class="log-viewer"] {{
            background: #0d1117;
            color: #c9d1d9;
            border: 1px solid {_BORDER};
            border-radius: 6px;
            font-family: Consolas, "Courier New", monospace;
            font-size: 12px;
            padding: 8px;
        }}
        """
    )


def section_title_html(text: str) -> str:
    """Dashboard / tab intro line (RichText QLabel)."""

    esc = (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
    return f'<span style="font-size:16px;font-weight:700;color:#3db8c6">{esc}</span>'
