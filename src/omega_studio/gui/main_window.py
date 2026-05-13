"""Primary PySide6 window — Dashboard, Models, Downloads, Settings, Advanced, Server, Logs."""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from omega_studio.gui.theme import apply_studio_theme, section_title_html
from omega_studio.gui.widgets.backend_env_tab import BackendEnvTab
from omega_studio.gui.widgets.playground_tab import PlaygroundTab
from omega_studio.gui.widgets.sidebar import Sidebar
from omega_studio.gui.widgets.slider_row import SliderRow
from omega_studio.gui.widgets.toast import ToastManager
from omega_studio.gui.workers import (
    HfDownloadWorker,
    LogReaderWorker,
    ModelSyncWorker,
    ServerPatchWorker,
    UrlDownloadWorker,
)
from omega_studio.gui_settings_store import (
    WindowState,
    gui_settings_to_child_env,
    load_gui_settings,
    save_gui_settings,
)
from omega_studio.paths import app_data_dir, bundle_root, ensure_app_dirs, portable_server_exe
from omega_studio.registry import load_registry, merge_scan_into_registry, save_registry

# Page indices (must match Sidebar._PAGES order)
_PAGE_DASHBOARD = 0
_PAGE_MODELS = 1
_PAGE_PLAYGROUND = 2
_PAGE_DOWNLOADS = 3
_PAGE_SETTINGS = 4
_PAGE_BACKEND = 5
_PAGE_ADVANCED = 6
_PAGE_SERVER = 7
_PAGE_LOGS = 8


def _vendor_llamacpp_vulkan_bin(bundle: Path) -> Path | None:
    """Prefer ``vendor/accelerators/...``; fall back to legacy ``vendor/lemonade/...``."""
    for sub in ("accelerators", "lemonade"):
        vk = bundle / "vendor" / sub / "bin" / "llamacpp" / "vulkan"
        if vk.is_dir():
            return vk
    return None


def _html_escape_plain(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


class _ActionCard(QFrame):
    """Clickable dashboard card (avoids QPushButton child-widget paint bugs)."""

    clicked = Signal()

    def __init__(self, icon: str, label: str, desc: str) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(90)
        self.setStyleSheet(
            "QFrame {"
            "  background:#232833;"
            "  border:1px solid #3d4656;"
            "  border-radius:10px;"
            "}"
            "QFrame:hover {"
            "  background:#2a3140;"
            "  border:1px solid #3db8c6;"
            "}"
        )
        lay = QVBoxLayout(self)
        lay.setSpacing(4)
        lay.setContentsMargins(12, 10, 12, 10)
        t = QLabel(
            f'<span style="font-size:18px">{icon}</span> '
            f'<span style="font-size:14px;font-weight:700;color:#e8eaf0;">{label}</span>'
        )
        t.setTextFormat(Qt.TextFormat.RichText)
        t.setStyleSheet("background:transparent;")
        d = QLabel(desc)
        d.setStyleSheet("background:transparent;color:#9aa3b8;font-size:12px;")
        d.setWordWrap(True)
        lay.addWidget(t)
        lay.addWidget(d)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Omega Runtime Studio")
        self.resize(1280, 820)
        self.setMinimumSize(1024, 700)

        self._last_model_table_rows: int = 0
        self._model_rows: list[dict[str, str]] = []

        self._server_proc: subprocess.Popen | None = None
        self._log_reader: LogReaderWorker | None = None
        self._hf_worker: HfDownloadWorker | None = None
        self._url_worker: UrlDownloadWorker | None = None
        self._sync_worker: ModelSyncWorker | None = None
        self._patch_worker: ServerPatchWorker | None = None
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._refresh_models_if_alive)
        self._restoring_session = False
        self._shutting_down = False

        self._toasts = ToastManager(self)

        self.setStatusBar(QStatusBar(self))

        # --- Sidebar + stacked pages ---
        self._sidebar = Sidebar()
        self._sidebar.page_changed.connect(self._on_page_changed)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_dashboard())
        self._stack.addWidget(self._build_models())
        self._playground = PlaygroundTab()
        self._stack.addWidget(self._playground)
        self._stack.addWidget(self._build_downloads())
        self._stack.addWidget(self._build_settings())
        self._backend_env = BackendEnvTab()
        self._stack.addWidget(self._backend_env)
        self._stack.addWidget(self._build_advanced())
        self._stack.addWidget(self._build_server())
        self._stack.addWidget(self._build_logs())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._sidebar)
        splitter.addWidget(self._stack)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setHandleWidth(1)

        # --- Header bar ---
        header = QFrame()
        header.setProperty("class", "header-bar")
        header.setFixedHeight(44)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(10)

        title = QLabel("Omega Runtime Studio")
        title.setProperty("class", "header-title")
        hl.addWidget(title)
        hl.addStretch()

        self._lbl_header_status = QLabel("API: stopped")
        self._lbl_header_status.setProperty("class", "header-status")
        hl.addWidget(self._lbl_header_status)

        btn_start = QPushButton("Start API")
        btn_start.setProperty("class", "tool")
        btn_start.setToolTip("Start the local HTTP API (Ctrl+Shift+S)")
        btn_start.setShortcut(QKeySequence("Ctrl+Shift+S"))
        btn_start.clicked.connect(self._start_server)
        hl.addWidget(btn_start)

        btn_refresh = QPushButton("Update models")
        btn_refresh.setProperty("class", "tool")
        btn_refresh.setToolTip("Refresh models (F5)")
        btn_refresh.setShortcut(QKeySequence("F5"))
        btn_refresh.clicked.connect(self._toolbar_refresh_models)
        hl.addWidget(btn_refresh)
        # Extra shortcut for Ctrl+R
        self._sc_refresh = QShortcut(QKeySequence(QKeySequence.StandardKey.Refresh), self)
        self._sc_refresh.activated.connect(self._toolbar_refresh_models)

        btn_chat = QPushButton("Open chat")
        btn_chat.setProperty("class", "tool")
        btn_chat.setToolTip("Jump to Playground (Ctrl+Alt+P)")
        btn_chat.setShortcut(QKeySequence("Ctrl+Alt+P"))
        btn_chat.clicked.connect(self._go_playground_page)
        hl.addWidget(btn_chat)

        # Root layout
        root = QWidget()
        rl = QVBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addWidget(header)
        rl.addWidget(splitter, stretch=1)

        self.setCentralWidget(root)

        self._load_settings_ui()
        self._playground._reload_models()
        self._restoring_session = True
        try:
            self._restore_session_ui()
        finally:
            self._restoring_session = False

        self._playground.cb_model.currentTextChanged.connect(self._persist_playground_prefs)
        self._playground.sp_max_tokens.valueChanged.connect(self._persist_playground_prefs)
        self._playground.chk_show_thinking.stateChanged.connect(self._persist_playground_prefs)

        self._refresh_models_table()

        self._sync_status_bar(
            "Shortcuts: F5 update · Ctrl+Shift+S start API · Ctrl+Alt+P chat · Ctrl+Enter send",
        )
        self._update_header_status()
        self._update_dashboard_status()

    # --- Navigation / session ---
    def _on_page_changed(self, idx: int) -> None:
        self._stack.setCurrentIndex(idx)
        if self._restoring_session:
            return
        gs = load_gui_settings()
        gs.main_tab_index = int(idx)
        save_gui_settings(gs)

    def _go_playground_page(self) -> None:
        self._sidebar.set_current(_PAGE_PLAYGROUND)
        self._on_page_changed(_PAGE_PLAYGROUND)

    def _sync_status_bar(self, message: str | None = None) -> None:
        sb = self.statusBar()
        if sb is None:
            return

        srv = ""
        if self._server_proc is not None and self._server_proc.poll() is None:
            srv = f"Local API: running (pid {self._server_proc.pid})"
        else:
            srv = "Local API: stopped — open Server tab or use Start API"

        row_line = (
            "No models in the list yet — try Discover on disk (Models tab)"
            if self._last_model_table_rows == 0
            else f"{self._last_model_table_rows} models listed"
        )
        idle = message or ""

        combo = srv
        combo += " · "
        combo += row_line
        if idle:
            combo += " · "
            combo += idle
        sb.showMessage(combo, 0)

    def _update_header_status(self) -> None:
        if self._server_proc is not None and self._server_proc.poll() is None:
            self._lbl_header_status.setText(
                f"<span style='color:#4ade80;'>\u25cf</span> API running "
                f"(pid {self._server_proc.pid})"
            )
        else:
            self._lbl_header_status.setText(
                "<span style='color:#f87171;'>\u25cf</span> API stopped"
            )

    def _toolbar_refresh_models(self) -> None:
        self._refresh_models_table()
        self._playground._reload_models()

    # --- Logging helpers ---
    def _log_line(self, text: str, *, tag: str = "INFO") -> None:
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] [{tag}] {text}"
        self._log_buffer.append((tag, line))
        _MAX_LOG_BUFFER = 5000
        if len(self._log_buffer) > _MAX_LOG_BUFFER:
            self._log_buffer = self._log_buffer[-_MAX_LOG_BUFFER:]
        self._apply_log_filter()
        if self._chk_log_autoscroll.isChecked():
            self._txt_logs.moveCursor(self._txt_logs.textCursor().MoveOperation.End)

    def _log_api(self, method: str, url: str, status: int | None = None) -> None:
        if status is None:
            self._log_line(f"→ {method} {url}", tag="API")
        else:
            self._log_line(f"← {method} {url} — {status}", tag="API")

    def _apply_log_filter(self) -> None:
        active = self._log_filter_current
        lines = [ln for tag, ln in self._log_buffer if active == "ALL" or tag == active]
        self._txt_logs.setPlainText("\n".join(lines))
        if self._chk_log_autoscroll.isChecked():
            self._txt_logs.moveCursor(self._txt_logs.textCursor().MoveOperation.End)

    # --- Dashboard ---
    def _build_dashboard(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(18)
        lay.setContentsMargins(24, 24, 24, 24)

        kicker = QLabel("LOCAL WORKSPACE")
        kicker.setProperty("class", "kicker")
        lay.addWidget(kicker)

        title = QLabel(section_title_html("Omega Runtime Studio") + "<br/>")
        title.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(title)

        # --- Status card ---
        status_card = QFrame()
        status_card.setProperty("class", "card")
        sc = QHBoxLayout(status_card)
        sc.setContentsMargins(18, 16, 18, 16)
        sc.setSpacing(20)

        self._dash_status_dot = QLabel("\u25cf")
        self._dash_status_dot.setStyleSheet("font-size:32px;color:#f87171;")
        self._dash_status_text = QLabel("API is stopped")
        self._dash_status_text.setStyleSheet("font-size:16px;font-weight:700;color:#e8eaf0;")
        self._dash_models_count = QLabel("0 models")
        self._dash_models_count.setStyleSheet("font-size:13px;color:#9aa3b8;")
        self._dash_hint = QLabel("Start the API to begin chatting.")
        self._dash_hint.setStyleSheet("font-size:12px;color:#9aa3b8;")
        self._dash_hint.setWordWrap(True)

        left = QVBoxLayout()
        left.addWidget(self._dash_status_text)
        left.addWidget(self._dash_models_count)
        left.addWidget(self._dash_hint)
        sc.addWidget(self._dash_status_dot)
        sc.addLayout(left, stretch=1)
        lay.addWidget(status_card)

        # --- Quick actions ---
        actions = QHBoxLayout()
        actions.setSpacing(14)

        def _make_card(icon: str, label: str, desc: str, slot):
            card = _ActionCard(icon, label, desc)
            card.clicked.connect(slot)
            return card

        actions.addWidget(
            _make_card("\u25c9", "Discover", "Scan folders and register new models.", self._on_scan)
        )
        actions.addWidget(
            _make_card(
                "\u25ce", "Start API", "Launch the local OpenAI-compatible server.",
                self._start_server,
            )
        )
        actions.addWidget(
            _make_card(
                "\u2756", "Open Chat", "Jump to Playground and test a model.",
                self._go_playground_page,
            )
        )
        lay.addLayout(actions)

        # --- Paths card ---
        paths = QGroupBox("Where files live")
        pf = QVBoxLayout(paths)
        cap_paths = QLabel(
            "Bundle = the app's runtime and tools. App data = your registry and preferences. "
            "You rarely need to edit these by hand."
        )
        cap_paths.setWordWrap(True)
        cap_paths.setTextFormat(Qt.TextFormat.PlainText)
        cap_paths.setProperty("class", "muted")
        self.lbl_bundle = QLabel()
        self.lbl_bundle.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_data = QLabel()
        self.lbl_data.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        pf.addWidget(cap_paths)
        pf.addWidget(self.lbl_bundle)
        pf.addWidget(self.lbl_data)
        lay.addWidget(paths)

        hint = QFrame()
        hint.setProperty("class", "card")
        hl = QVBoxLayout(hint)
        hl.setContentsMargins(18, 14, 18, 14)
        hl.setSpacing(8)
        tip = QLabel(
            "<b>Using another app</b> (Cursor, VS Code, scripts): point it at "
            "<b>http://127.0.0.1:&lt;port&gt;/v1</b> — the port is on the <b>Server</b> tab. "
            "That is the same address this Playground uses."
        )
        tip.setTextFormat(Qt.TextFormat.RichText)
        tip.setWordWrap(True)
        hl.addWidget(tip)
        lay.addWidget(hint)

        lay.addStretch()
        self._refresh_dashboard_labels()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(w)
        return scroll

    def _update_dashboard_status(self) -> None:
        running = self._server_proc is not None and self._server_proc.poll() is None
        if running:
            self._dash_status_dot.setStyleSheet("font-size:32px;color:#4ade80;")
            self._dash_status_text.setText("API is running")
            self._dash_hint.setText("Everything looks good. Head to Playground to chat.")
        else:
            self._dash_status_dot.setStyleSheet("font-size:32px;color:#f87171;")
            self._dash_status_text.setText("API is stopped")
            self._dash_hint.setText("Start the API to begin chatting.")
        self._dash_models_count.setText(
            f"{self._last_model_table_rows} model(s) registered"
        )

    def _refresh_dashboard_labels(self) -> None:
        br = bundle_root()
        ad = app_data_dir()
        self.lbl_bundle.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_data.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_bundle.setText(
            "<b>Bundle root</b> (OMEGA_BUNDLE_ROOT)<br/>"
            f"<span style=\"font-family:monospace\">{_html_escape_plain(str(br))}</span>"
        )
        self.lbl_data.setText(
            "<b>App data</b> (registry &amp; prefs)<br/>"
            f"<span style=\"font-family:monospace\">{_html_escape_plain(str(ad))}</span>"
        )

    # --- Session persistence (gui_settings.json + registry.json) ---
    def _restore_session_ui(self) -> None:
        gs = load_gui_settings()
        w = gs.window
        if w.width and w.height and w.width >= 400 and w.height >= 300:
            self.setGeometry(int(w.x or 80), int(w.y or 80), int(w.width), int(w.height))
        if w.maximized:
            self.showMaximized()
        idx = max(0, min(int(gs.main_tab_index), self._stack.count() - 1))
        self._sidebar.set_current(idx)
        self._stack.setCurrentIndex(idx)
        self._restore_playground_from_gui_settings(gs)
        self.chk_sync_runtime.setChecked(bool(gs.sync_runtime_on_apply))
        self._try_select_model_row(gs.last_selected_model_id)

    def _try_select_model_row(self, mid: str) -> None:
        mid = (mid or "").strip()
        if not mid:
            return
        for row in range(self.table_models.rowCount()):
            it = self.table_models.item(row, 0)
            if it and it.text().strip() == mid:
                self.table_models.blockSignals(True)
                self.table_models.selectRow(row)
                self.table_models.blockSignals(False)
                break

    def _restore_playground_from_gui_settings(self, gs) -> None:
        mid = (gs.playground_model_id or "").strip()
        if mid:
            i = self._playground.cb_model.findText(mid)
            if i >= 0:
                self._playground.cb_model.setCurrentIndex(i)
            else:
                self._playground.cb_model.setCurrentText(mid)
        if gs.playground_max_tokens is not None:
            self._playground.sp_max_tokens.setValue(int(gs.playground_max_tokens))
        if gs.playground_show_thinking is not None:
            self._playground.chk_show_thinking.setChecked(bool(gs.playground_show_thinking))

    def _persist_playground_prefs(self, *_args) -> None:
        if self._restoring_session:
            return
        gs = load_gui_settings()
        self._snapshot_playground_to_gui_settings(gs)
        save_gui_settings(gs)

    def _persist_sync_runtime_pref(self, *_args) -> None:
        if self._restoring_session:
            return
        gs = load_gui_settings()
        gs.sync_runtime_on_apply = bool(self.chk_sync_runtime.isChecked())
        save_gui_settings(gs)

    def _snapshot_playground_to_gui_settings(self, gs) -> None:
        gs.playground_model_id = self._playground.cb_model.currentText().strip()
        gs.playground_max_tokens = int(self._playground.sp_max_tokens.value())
        gs.playground_show_thinking = bool(self._playground.chk_show_thinking.isChecked())

    def _snapshot_downloads_to_gui_settings(self, gs) -> None:
        gs.downloads_hf_repo = self.le_hf_repo.text()
        gs.downloads_hf_file = self.le_hf_file.text()
        gs.downloads_dest = self.le_dest.text()
        gs.downloads_url = self.le_url.text()

    def _load_downloads_from_gui_settings(self) -> None:
        gs = load_gui_settings()
        if (gs.downloads_hf_repo or "").strip():
            self.le_hf_repo.setText(gs.downloads_hf_repo)
        if (gs.downloads_hf_file or "").strip():
            self.le_hf_file.setText(gs.downloads_hf_file)
        if (gs.downloads_dest or "").strip():
            self.le_dest.setText(gs.downloads_dest)
        if (gs.downloads_url or "").strip():
            self.le_url.setText(gs.downloads_url)

    def _persist_last_model_selection(self) -> None:
        if self._restoring_session:
            return
        mid = self._selected_model_id_from_table()
        if not mid:
            return
        gs = load_gui_settings()
        gs.last_selected_model_id = mid
        save_gui_settings(gs)

    def _persist_window_and_session(self) -> None:
        gs = load_gui_settings()
        ng = self.normalGeometry()
        gs.window = WindowState(
            x=int(ng.x()),
            y=int(ng.y()),
            width=max(400, int(ng.width())),
            height=max(300, int(ng.height())),
            maximized=bool(self.isMaximized()),
        )
        gs.main_tab_index = int(self._stack.currentIndex())
        mid = self._selected_model_id_from_table()
        if mid:
            gs.last_selected_model_id = mid
        gs.sync_runtime_on_apply = bool(self.chk_sync_runtime.isChecked())
        self._snapshot_downloads_to_gui_settings(gs)
        self._snapshot_playground_to_gui_settings(gs)
        save_gui_settings(gs)

    def _persist_server_registry(self) -> None:
        reg = load_registry()
        reg.settings.server_host = self.le_host.text().strip()
        reg.settings.server_port = int(self.sp_port.value())
        save_registry(reg)

    # --- Presets ---
    def _apply_preset(self, name: str) -> None:
        presets: dict[str, dict[str, float]] = {
            "Default": {"ctx": 4096, "gpu": -1, "temp": 0.7, "top_p": 0.9},
            "Creative": {"ctx": 8192, "gpu": -1, "temp": 1.0, "top_p": 0.95},
            "Balanced": {"ctx": 4096, "gpu": -1, "temp": 0.7, "top_p": 0.9},
            "Precise": {"ctx": 4096, "gpu": -1, "temp": 0.2, "top_p": 0.5},
        }
        p = presets.get(name, presets["Default"])
        self.slider_ctx.set_value(p["ctx"])
        self.slider_gpu.set_value(p["gpu"])
        self.slider_temp.set_value(p["temp"])
        self.slider_top_p.set_value(p["top_p"])
        self._highlight_preset(name)

    def _highlight_preset(self, name: str) -> None:
        for btn in self._preset_buttons:
            active = btn.text() == name
            btn.setProperty("class", "preset-active" if active else "preset")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn.update()

    # --- Models ---
    def _build_models(self) -> QWidget:
        inner = QWidget()
        outer = QVBoxLayout(inner)
        outer.setSpacing(14)
        outer.setContentsMargins(20, 20, 20, 20)

        intro = QLabel(
            "Your registered models appear in the table. "
            "Click a row to tune it. <b>Loaded</b> shows whether the running server has that "
            "model in memory. <b>offline</b> means the API was not reached."
        )
        intro.setTextFormat(Qt.TextFormat.RichText)
        intro.setWordWrap(True)
        intro.setProperty("class", "muted")
        outer.addWidget(intro)

        ctrl = QHBoxLayout()
        self.btn_scan = QPushButton("Discover on disk")
        self.btn_scan.setToolTip(
            "Look inside the folders configured in Settings, find new weight files, "
            "and add them to the registry."
        )
        self.btn_scan.clicked.connect(self._on_scan)
        self.btn_refresh_api = QPushButton("Sync with server")
        self.btn_refresh_api.setToolTip(
            "Ask the running API for its model list and merge with the table."
        )
        self.btn_refresh_api.clicked.connect(self._refresh_models_table)
        ctrl.addWidget(self.btn_scan)
        ctrl.addWidget(self.btn_refresh_api)
        ctrl.addStretch()

        # Search box
        self._le_model_search = QLineEdit()
        self._le_model_search.setPlaceholderText("Search models…")
        self._le_model_search.setMaximumWidth(260)
        self._le_model_search.textChanged.connect(self._apply_model_search)
        ctrl.addWidget(self._le_model_search)
        outer.addLayout(ctrl)

        self.lbl_models_status = QLabel("Checking whether the local API answers…")
        self.lbl_models_status.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_models_status.setWordWrap(True)
        self.lbl_models_status.setProperty("class", "muted")
        outer.addWidget(self.lbl_models_status)

        self.table_models = QTableWidget(0, 6)
        self.table_models.setHorizontalHeaderLabels(
            ["Model id", "Format", "Accelerator", "VRAM est. (MB)", "Pinned", "Loaded"]
        )
        hdr_tips = [
            "Name exposed to clients (matches your files / server).",
            "Weight layout, e.g. gguf / onnx.",
            "Which device stack to prefer for this model.",
            "Rough memory estimate when known.",
            "Pin important models so automatic unload skips them.",
            "Whether the live server currently holds this model (needs API + Sync).",
        ]
        for col, ht in enumerate(hdr_tips):
            hi = self.table_models.horizontalHeaderItem(col)
            if hi is not None:
                hi.setToolTip(ht)
        self.table_models.setAlternatingRowColors(True)
        self.table_models.verticalHeader().setVisible(False)
        self.table_models.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_models.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table_models.setMinimumHeight(240)
        self.table_models.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.table_models.itemSelectionChanged.connect(self._on_models_selection_changed)
        outer.addWidget(self.table_models, stretch=1)

        # --- Tuning card (LM Studio–style) ---
        tuning = QGroupBox("Tuning — selected model")
        tl = QVBoxLayout(tuning)
        tl.setSpacing(10)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(6)
        preset_lbl = QLabel("Presets")
        preset_lbl.setStyleSheet("font-weight:600;color:#9aa3b8;")
        preset_row.addWidget(preset_lbl)
        self._preset_buttons: list[QPushButton] = []
        for pname in ("Default", "Creative", "Balanced", "Precise"):
            btn = QPushButton(pname)
            btn.setProperty("class", "preset")
            btn.setCheckable(False)
            btn.clicked.connect(lambda _c=False, n=pname: self._apply_preset(n))
            self._preset_buttons.append(btn)
            preset_row.addWidget(btn)
        preset_row.addStretch()
        tl.addLayout(preset_row)

        self.slider_ctx = SliderRow("Context length", 256, 131072, step=256, suffix=" tokens")
        self.slider_gpu = SliderRow(
            "GPU offload", -1, 100, step=1, suffix=" layers", special_value_text="Auto"
        )
        self.slider_temp = SliderRow("Temperature", 0.0, 2.0, step=0.05, decimals=2)
        self.slider_top_p = SliderRow("Top P", 0.0, 1.0, step=0.01, decimals=2)

        tl.addWidget(self.slider_ctx)
        tl.addWidget(self.slider_gpu)
        tl.addWidget(self.slider_temp)
        tl.addWidget(self.slider_top_p)

        more = QHBoxLayout()
        more.setSpacing(12)
        self.sp_ui_n_batch = QSpinBox()
        self.sp_ui_n_batch.setRange(1, 8192)
        self.sp_ui_max_tokens = QSpinBox()
        self.sp_ui_max_tokens.setRange(1, 131072)
        self.sp_ui_max_tokens.setValue(256)
        more.addWidget(QLabel("Batch size"))
        more.addWidget(self.sp_ui_n_batch)
        more.addSpacing(10)
        more.addWidget(QLabel("Max tokens"))
        more.addWidget(self.sp_ui_max_tokens)
        more.addStretch()
        tl.addLayout(more)

        self.chk_sync_runtime = QCheckBox(
            "Also push these values to the running server (PATCH) when the API is up"
        )
        self.chk_sync_runtime.stateChanged.connect(self._persist_sync_runtime_pref)
        tl.addWidget(self.chk_sync_runtime)

        btn_apply_inf = QPushButton("Save tuning")
        btn_apply_inf.setToolTip(
            "Persist ui_overrides for this model and optionally PATCH the server."
        )
        btn_apply_inf.clicked.connect(self._apply_model_inference)
        tl.addWidget(btn_apply_inf)
        outer.addWidget(tuning)

        # --- Registry fields ---
        edit = QGroupBox("Registry fields — selected model")
        ef = QFormLayout(edit)
        self.le_edit_mid = QLineEdit()
        self.cb_accel = QComboBox()
        self.cb_accel.addItems(
            ["auto", "cpu", "cuda", "dml", "openvino", "qnn", "vitis", "accelerators"]
        )
        self.chk_pin = QCheckBox("Pin model")
        self.chk_pin.setToolTip(
            "Pinned models are treated as important when the server frees memory (LRU policy)."
        )
        btn_apply = QPushButton("Save registry fields")
        btn_apply.clicked.connect(self._apply_model_fields)
        ef.addRow("Model id", self.le_edit_mid)
        ef.addRow("Accelerator", self.cb_accel)
        ef.addRow(self.chk_pin)
        ef.addRow(btn_apply)
        outer.addWidget(edit)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(inner)
        return scroll

    def _apply_model_search(self, text: str) -> None:
        text = text.strip().lower()
        self.table_models.setRowCount(0)
        filtered = [r for r in self._model_rows if not text or text in r["id"].lower()]
        self.table_models.setRowCount(len(filtered))
        for i, row in enumerate(filtered):
            self._set_model_row(i, row)
        self.table_models.horizontalHeader().setStretchLastSection(True)
        self.table_models.resizeColumnsToContents()
        self._last_model_table_rows = len(self._model_rows)
        self._sync_status_bar()
        self._update_dashboard_status()

    def _set_model_row(self, i: int, row: dict[str, str]) -> None:
        self.table_models.setItem(i, 0, QTableWidgetItem(row["id"]))
        self.table_models.setItem(i, 1, QTableWidgetItem(row["format"]))
        self.table_models.setItem(i, 2, QTableWidgetItem(row["accelerator"]))
        self.table_models.setItem(i, 3, QTableWidgetItem(row["vram"]))
        self.table_models.setItem(i, 4, self._pill_item(row["pinned"], "teal"))
        loaded_color = (
            "green"
            if row["loaded"] == "yes"
            else "amber"
            if row["loaded"] == "offline"
            else "gray"
        )
        self.table_models.setItem(i, 5, self._pill_item(row["loaded"], loaded_color))

    def _pill_item(self, text: str, color: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        colors = {
            "green": "#4ade80",
            "amber": "#fbbf24",
            "gray": "#9aa3b8",
            "teal": "#3db8c6",
        }
        c = colors.get(color, "#9aa3b8")
        item.setForeground(QColor(c))
        if text:
            item.setToolTip(text)
        return item

    def _selected_model_id_from_table(self) -> str:
        rows = self.table_models.selectionModel().selectedRows()
        if not rows:
            return ""
        r = rows[0].row()
        it = self.table_models.item(r, 0)
        return it.text().strip() if it else ""

    def _on_models_selection_changed(self) -> None:
        mid = self._selected_model_id_from_table()
        self.le_edit_mid.setText(mid)
        if not mid:
            return
        self._persist_last_model_selection()
        reg = load_registry()
        if mid not in reg.models:
            return
        self._load_inference_ui(mid, reg)
        rec = reg.models[mid]
        acc = (rec.accelerator or "auto").strip() or "auto"
        if acc == "lemonade":
            acc = "accelerators"
        idx = self.cb_accel.findText(acc)
        if idx >= 0:
            self.cb_accel.setCurrentIndex(idx)
        self.chk_pin.setChecked(bool(rec.pinned))

    def _load_inference_ui(self, mid: str, reg) -> None:
        st = reg.settings
        rec = reg.models[mid]
        ui = dict(rec.ui_overrides or {})

        def pick(keys: tuple[str, ...], default: float | int) -> float | int:
            for k in keys:
                if k in ui:
                    return ui[k]  # type: ignore[no-any-return]
            return default

        self.slider_ctx.set_value(float(pick(("n_ctx",), st.n_ctx)))
        self.slider_gpu.set_value(float(pick(("n_gpu_layers",), st.n_gpu_layers)))
        self.sp_ui_n_batch.setValue(int(pick(("n_batch", "batch"), st.batch)))
        self.slider_temp.set_value(float(pick(("temperature",), st.temperature)))
        self.slider_top_p.set_value(float(pick(("top_p",), st.top_p)))
        self.sp_ui_max_tokens.setValue(int(pick(("max_tokens",), 256)))
        self._highlight_preset("")

    def _apply_model_inference(self) -> None:
        mid = self.le_edit_mid.text().strip() or self._selected_model_id_from_table()
        if not mid:
            QMessageBox.warning(self, "Model", "Select a model row or enter a model id.")
            return
        reg = load_registry()
        if mid not in reg.models:
            QMessageBox.warning(self, "Model", f"Unknown id: {mid}")
            return
        rec = reg.models[mid]
        rec.ui_overrides = {
            "n_ctx": int(self.slider_ctx.value()),
            "n_gpu_layers": int(self.slider_gpu.value()),
            "n_batch": self.sp_ui_n_batch.value(),
            "temperature": float(self.slider_temp.value()),
            "top_p": float(self.slider_top_p.value()),
            "max_tokens": self.sp_ui_max_tokens.value(),
        }
        save_registry(reg)
        self._toasts.show(f"Saved tuning for {mid}", level="success")
        if self.chk_sync_runtime.isChecked():
            host = reg.settings.server_host
            port = reg.settings.server_port
            url = f"http://{host}:{port}/v1/studio/models/{mid}"
            headers: dict[str, str] = {}
            gs = load_gui_settings()
            if gs.omega_api_key.strip():
                headers["Authorization"] = f"Bearer {gs.omega_api_key.strip()}"
            self._log_api("PATCH", url)
            worker = ServerPatchWorker(
                url, {"ui_overrides": rec.ui_overrides}, headers
            )
            self._patch_worker = worker
            worker.succeeded.connect(lambda code: self._on_patch_succeeded(code, url))
            worker.http_error.connect(lambda code, body: self._on_patch_http_error(code, body, url))
            worker.failed.connect(lambda err: self._on_patch_failed(err, url))
            worker.finished.connect(worker.deleteLater)
            worker.start()
        self._playground._reload_models()
        self._refresh_models_table()

    def _on_patch_succeeded(self, code: int, url: str) -> None:
        self._log_api("PATCH", url, code)
        self._toasts.show("Runtime registry updated", level="success")
        self._patch_worker = None

    def _on_patch_http_error(self, code: int, body: str, url: str) -> None:
        self._log_api("PATCH", url, code)
        self._toasts.show(f"PATCH failed: {code}", level="error")
        self._patch_worker = None

    def _on_patch_failed(self, err: str, url: str) -> None:
        self._log_api("PATCH", url)
        self._toasts.show(f"PATCH error: {err}", level="error")
        self._patch_worker = None

    def _apply_model_fields(self) -> None:
        mid = self.le_edit_mid.text().strip()
        if not mid:
            QMessageBox.warning(self, "Model", "Enter a model id.")
            return
        reg = load_registry()
        if mid not in reg.models:
            QMessageBox.warning(self, "Model", f"Unknown id: {mid}")
            return
        rec = reg.models[mid]
        acc = self.cb_accel.currentText().strip()
        if acc == "lemonade":
            acc = "accelerators"
        rec.accelerator = acc
        rec.pinned = self.chk_pin.isChecked()
        save_registry(reg)
        self._toasts.show(f"Updated {mid} in registry", level="success")
        self._refresh_models_table()
        self._load_inference_ui(mid, load_registry())

    def _on_scan(self) -> None:
        reg = load_registry()
        reg, added = merge_scan_into_registry(reg)
        save_registry(reg)
        self._toasts.show(f"Discover finished — {added} new entries", level="success")
        self._refresh_models_table()
        self._playground._reload_models()
        self._update_dashboard_status()

    def _refresh_models_table(self) -> None:
        """Merge registry rows with `/v1/models` omega hints (no truncation).

        The HTTP call runs off the GUI thread via ModelSyncWorker so the UI
        never freezes, even when the server is unresponsive.
        """
        if self._sync_worker is not None and self._sync_worker.isRunning():
            return

        reg = load_registry()
        port = reg.settings.server_port
        host = reg.settings.server_host.strip() or "127.0.0.1"
        url = f"http://{host}:{port}/v1/models"

        hdrs: dict[str, str] = {}
        gs = load_gui_settings()
        if gs.omega_api_key.strip():
            hdrs["Authorization"] = f"Bearer {gs.omega_api_key.strip()}"

        self._log_api("GET", url)
        worker = ModelSyncWorker(url, hdrs)
        self._sync_worker = worker
        worker.succeeded.connect(self._on_sync_finished)
        worker.failed.connect(self._on_sync_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_sync_finished(self, result: dict[str, Any]) -> None:
        self._sync_worker = None
        api_ok = result.get("api_ok", False)
        api_by_id = result.get("api_by_id") or {}
        err_note = result.get("err_note", "")

        reg = load_registry()
        port = reg.settings.server_port
        host = reg.settings.server_host.strip() or "127.0.0.1"
        url = f"http://{host}:{port}/v1/models"

        if api_ok:
            self._log_api("GET", url, 200)
            self.lbl_models_status.setText(
                f"Connected to http://{host}:{port}/v1 — "
                f"{len(api_by_id)} advertised model(s), {len(reg.models)} in registry"
            )
        elif err_note:
            self.lbl_models_status.setText(
                f"Could not reach http://{host}:{port} ({err_note}). "
                f"Showing {len(reg.models)} registry row(s). "
                "Start the API (Server tab) to see live Loaded / Pinned columns."
            )
        else:
            self.lbl_models_status.setText(
                f"No live API enrichment; showing all {len(reg.models)} registry model(s)."
            )

        union_ids = sorted(set(reg.models.keys()) | set(api_by_id.keys()))
        rows: list[dict[str, str]] = []
        for mid in union_ids:
            rec = reg.models.get(mid)
            api_item = api_by_id.get(mid)
            omega = api_item.get("omega") if isinstance(api_item, dict) else {}

            fmt = str(omega.get("format") or ((rec.format or "") if rec else ""))
            acc = str(
                omega.get("accelerator") or ((rec.accelerator or "") if rec else "")
            ).strip()

            pv = omega.get("vram_estimate_mb")
            if pv is None and rec is not None:
                pv = getattr(rec, "vram_estimate_mb", None)

            pinned = ""
            if isinstance(omega.get("pinned"), bool):
                pinned = "yes" if omega["pinned"] else ""
            elif rec is not None and rec.pinned:
                pinned = "yes"

            loaded = ""
            if not api_ok:
                loaded = "offline"
            elif api_item is None:
                loaded = "(not advertised)"
            else:
                lg = omega.get("loaded")
                loaded = "yes" if lg else "no"

            rows.append(
                {
                    "id": mid or "—",
                    "format": fmt,
                    "accelerator": acc or "auto",
                    "vram": str(pv or ""),
                    "pinned": pinned,
                    "loaded": loaded,
                }
            )

        self._model_rows = rows
        self._apply_model_search(self._le_model_search.text())

        restore = ""
        le = getattr(self, "le_edit_mid", None)
        if isinstance(le, QLineEdit) and le.text().strip():
            restore = le.text().strip()
        if not restore:
            restore = load_gui_settings().last_selected_model_id or ""
        if restore:
            self._try_select_model_row(restore)

        self._update_dashboard_status()

    def _on_sync_failed(self, err: str) -> None:
        self._sync_worker = None
        reg = load_registry()
        self.lbl_models_status.setText(
            f"Sync failed ({err}). Showing {len(reg.models)} registry row(s)."
        )
        # Still build rows from registry alone
        self._on_sync_finished({"api_ok": False, "api_by_id": {}, "err_note": err})

    # --- Downloads ---
    def _build_downloads(self) -> QWidget:
        inner = QWidget()
        root_lay = QVBoxLayout(inner)
        root_lay.setSpacing(14)
        root_lay.setContentsMargins(20, 20, 20, 20)

        dl_intro = QLabel(
            "Fetch a single file from Hugging Face or a direct link into a folder on disk. "
            "After it finishes, use Discover on disk on the Models tab so the new file is "
            "registered."
        )
        dl_intro.setWordWrap(True)
        dl_intro.setProperty("class", "muted")
        root_lay.addWidget(dl_intro)

        st = QGroupBox("Download status · runs off the GUI thread")
        st_lay = QVBoxLayout(st)
        self.lbl_dl_status = QLabel(
            "Idle — you can switch pages while a download runs. After a new weight file lands, use "
            "<b>Models → Discover on disk</b>."
        )
        self.lbl_dl_status.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_dl_status.setWordWrap(True)
        self.progress_dl = QProgressBar()
        self.progress_dl.setRange(0, 100)
        self.progress_dl.setValue(0)
        self.progress_dl.setFormat("%p%")
        st_lay.addWidget(self.lbl_dl_status)
        st_lay.addWidget(self.progress_dl)
        root_lay.addWidget(st)

        hf = QGroupBox("Hugging Face · single file")
        hf_form = QFormLayout(hf)
        self.le_hf_repo = QLineEdit("meta-llama/Llama-3.2-1B-Instruct-GGUF")
        self.le_hf_file = QLineEdit("README.md")
        self.le_dest = QLineEdit(str(ensure_app_dirs()[1]))
        btn_pick = QPushButton("Browse…")
        btn_pick.clicked.connect(self._pick_dest)
        row_dest = QHBoxLayout()
        row_dest.addWidget(self.le_dest, stretch=1)
        row_dest.addWidget(btn_pick)
        hf_form.addRow("Repo id", self.le_hf_repo)
        hf_form.addRow("File in repo", self.le_hf_file)
        hf_form.addRow("Destination folder", row_dest)
        self.btn_hf_download = QPushButton("Start Hugging Face download")
        self.btn_hf_download.clicked.connect(self._run_hf_download)
        hf_form.addRow(self.btn_hf_download)
        root_lay.addWidget(hf)

        du = QGroupBox("Direct URL · resume-capable")
        du_form = QFormLayout(du)
        self.le_url = QLineEdit("https://example.com/model.gguf")
        self.btn_url_download = QPushButton("Start URL download")
        self.btn_url_download.clicked.connect(self._run_url_download)
        du_form.addRow("URL", self.le_url)
        du_form.addRow(self.btn_url_download)
        root_lay.addWidget(du)

        root_lay.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(inner)

        self._load_downloads_from_gui_settings()
        return scroll

    def _pick_dest(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Destination folder")
        if d:
            self.le_dest.setText(d)

    def _downloads_busy(self) -> bool:
        hf = self._hf_worker is not None and self._hf_worker.isRunning()
        ur = self._url_worker is not None and self._url_worker.isRunning()
        return hf or ur

    def _set_download_controls_busy(self, busy: bool) -> None:
        for b in (
            getattr(self, "btn_hf_download", None),
            getattr(self, "btn_url_download", None),
        ):
            if b is not None:
                b.setEnabled(not busy)

    def _on_hf_progress(self, ratio: float) -> None:
        pct = max(0, min(100, int(round(float(ratio) * 100.0))))
        self.progress_dl.setValue(pct)

    def _run_hf_download(self) -> None:
        if self._downloads_busy():
            QMessageBox.information(self, "Downloads", "A download is already running.")
            return
        repo = self.le_hf_repo.text().strip()
        fn = self.le_hf_file.text().strip()
        if not repo or not fn:
            QMessageBox.warning(self, "Downloads", "Enter both repo id and file name.")
            return
        raw_dest = self.le_dest.text().strip()
        if not raw_dest:
            QMessageBox.warning(self, "Downloads", "Choose a destination folder.")
            return
        dest = Path(raw_dest).expanduser()

        self.progress_dl.setRange(0, 100)
        self.progress_dl.setValue(0)
        self.lbl_dl_status.setText(f"Hugging Face download… <b>{repo}</b> · <code>{fn}</code>")
        self._set_download_controls_busy(True)

        worker = HfDownloadWorker(repo, fn, dest)
        self._hf_worker = worker
        worker.progress_ratio.connect(self._on_hf_progress)
        worker.succeeded.connect(self._hf_download_succeeded)
        worker.failed.connect(self._hf_download_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _hf_download_succeeded(self, path: object) -> None:
        self._set_download_controls_busy(False)
        self.progress_dl.setValue(100)
        pstr = Path(path).resolve()
        self.lbl_dl_status.setText(f"Finished — saved to:<br/><code>{pstr}</code>")
        self._toasts.show("Download complete", level="success")
        self._playground._reload_models()
        self._refresh_models_table()

    def _hf_download_failed(self, err: str) -> None:
        self._set_download_controls_busy(False)
        self.progress_dl.setValue(0)
        self.lbl_dl_status.setText("HF download failed (see dialog).")
        QMessageBox.critical(self, "Hugging Face download failed", err)

    def _run_url_download(self) -> None:
        if self._downloads_busy():
            QMessageBox.information(self, "Downloads", "A download is already running.")
            return
        url = self.le_url.text().strip()
        if not url:
            QMessageBox.warning(self, "Downloads", "Enter a URL.")
            return
        dest = Path(self.le_dest.text().strip()).expanduser() / Path(url.split("?", 1)[0]).name
        dest.parent.mkdir(parents=True, exist_ok=True)

        u_disp = url if len(url) <= 100 else url[:97] + "…"
        self.progress_dl.setRange(0, 100)
        self.progress_dl.setValue(0)
        self.lbl_dl_status.setText(f"Direct URL · <code>{u_disp}</code>")
        self._set_download_controls_busy(True)

        worker = UrlDownloadWorker(url, dest)
        self._url_worker = worker
        worker.progress_ratio.connect(self._on_hf_progress)
        worker.succeeded.connect(self._url_download_succeeded)
        worker.failed.connect(self._url_download_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _url_download_succeeded(self, path: object) -> None:
        self._set_download_controls_busy(False)
        self.progress_dl.setValue(100)
        pstr = Path(path).resolve()
        self.lbl_dl_status.setText(f"Finished — saved to:<br/><code>{pstr}</code>")
        self._toasts.show("Download complete", level="success")
        self._playground._reload_models()
        self._refresh_models_table()

    def _url_download_failed(self, err: str) -> None:
        self._set_download_controls_busy(False)
        self.progress_dl.setValue(0)
        self.lbl_dl_status.setText("URL download failed (see dialog).")
        QMessageBox.critical(self, "URL download failed", err)

    # --- Settings ---
    def _build_settings(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(20, 20, 20, 20)
        intro_s = QLabel(
            "Baseline defaults for the engine (context length, temperature, threads, …) and "
            "where to look for model files. Saved to registry.json — after changing folders, "
            "use Discover on disk on the Models tab."
        )
        intro_s.setTextFormat(Qt.TextFormat.PlainText)
        intro_s.setWordWrap(True)
        intro_s.setProperty("class", "muted")
        form.addRow(intro_s)
        self.sp_n_ctx = QSpinBox()
        self.sp_n_ctx.setRange(256, 131072)
        self.sp_temp = QDoubleSpinBox()
        self.sp_temp.setRange(0.0, 2.0)
        self.sp_temp.setSingleStep(0.05)
        self.sp_top_p = QDoubleSpinBox()
        self.sp_top_p.setRange(0.0, 1.0)
        self.sp_top_p.setSingleStep(0.01)
        self.sp_gpu = QSpinBox()
        self.sp_gpu.setRange(-1, 256)
        self.sp_batch = QSpinBox()
        self.sp_batch.setRange(1, 8192)
        self.sp_threads = QSpinBox()
        self.sp_threads.setRange(1, 128)
        self.sp_max_models = QSpinBox()
        self.sp_max_models.setRange(1, 15)
        self.chk_lru = QCheckBox("Enable LRU eviction")

        form.addRow("n_ctx", self.sp_n_ctx)
        form.addRow("temperature", self.sp_temp)
        form.addRow("top_p", self.sp_top_p)
        form.addRow("n_gpu_layers (-1 = auto)", self.sp_gpu)
        form.addRow("batch", self.sp_batch)
        form.addRow("threads", self.sp_threads)
        form.addRow("max concurrent models", self.sp_max_models)
        form.addRow(self.chk_lru)

        folders = QGroupBox("Model folders (one line each)")
        fv = QVBoxLayout(folders)
        self.txt_folders = QPlainTextEdit()
        self.txt_folders.setPlaceholderText(str(ensure_app_dirs()[1]))
        fv.addWidget(self.txt_folders)
        form.addRow(folders)

        btn_save = QPushButton("Save settings")
        btn_save.clicked.connect(self._save_settings)
        form.addRow(btn_save)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(w)
        return scroll

    def _load_settings_ui(self) -> None:
        reg = load_registry()
        st = reg.settings
        self.sp_n_ctx.setValue(st.n_ctx)
        self.sp_temp.setValue(st.temperature)
        self.sp_top_p.setValue(st.top_p)
        self.sp_gpu.setValue(st.n_gpu_layers)
        self.sp_batch.setValue(st.batch)
        self.sp_threads.setValue(st.threads)
        self.sp_max_models.setValue(st.max_concurrent_models)
        self.chk_lru.setChecked(st.lru_eviction_enabled)
        self.txt_folders.setPlainText("\n".join(reg.model_folders))

    def _save_settings(self) -> None:
        reg = load_registry()
        reg.settings.n_ctx = self.sp_n_ctx.value()
        reg.settings.temperature = float(self.sp_temp.value())
        reg.settings.top_p = float(self.sp_top_p.value())
        reg.settings.n_gpu_layers = self.sp_gpu.value()
        reg.settings.batch = self.sp_batch.value()
        reg.settings.threads = self.sp_threads.value()
        reg.settings.max_concurrent_models = self.sp_max_models.value()
        reg.settings.lru_eviction_enabled = self.chk_lru.isChecked()
        lines = [ln.strip() for ln in self.txt_folders.toPlainText().splitlines() if ln.strip()]
        reg.model_folders = lines or reg.model_folders
        save_registry(reg)
        self._toasts.show("Settings saved to registry.json", level="success")
        self._playground._reload_models()
        self._refresh_models_table()

    # --- Advanced ---
    def _build_advanced(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 20, 20, 20)
        intro_a = QLabel(
            "Expert only: raw JSON merged into the server-side registry. Invalid JSON is rejected. "
            "Prefer the Models tab for normal tuning."
        )
        intro_a.setTextFormat(Qt.TextFormat.PlainText)
        intro_a.setWordWrap(True)
        intro_a.setProperty("class", "muted")
        lay.addWidget(intro_a)
        lay.addWidget(QLabel("Per-model overrides JSON (model_id → params):"))
        self.txt_overrides = QPlainTextEdit()
        self.txt_overrides.setPlaceholderText('{\n  "my-model": {"n_ctx": 4096}\n}')
        lay.addWidget(self.txt_overrides)
        btn = QPushButton("Save overrides")
        btn.clicked.connect(self._save_overrides)
        lay.addWidget(btn)
        reg = load_registry()
        self.txt_overrides.setPlainText(json.dumps(reg.settings.per_model_overrides, indent=2))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(w)
        return scroll

    def _save_overrides(self) -> None:
        try:
            data = json.loads(self.txt_overrides.toPlainText() or "{}")
            if not isinstance(data, dict):
                raise ValueError("root must be an object")
        except Exception as exc:
            QMessageBox.critical(self, "Invalid JSON", str(exc))
            return
        reg = load_registry()
        reg.settings.per_model_overrides = {str(k): dict(v) for k, v in data.items()}
        save_registry(reg)
        self._toasts.show("Overrides saved", level="success")

    # --- Server ---
    def _build_server(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(20, 20, 20, 20)
        srv_intro = QLabel(
            "Starts the same OpenAI-compatible HTTP API that the Playground and external clients "
            "use. Nothing answers on this address until you start it. Leave host/port unchanged "
            "unless you know you need a different bind."
        )
        srv_intro.setWordWrap(True)
        srv_intro.setProperty("class", "muted")
        form.addRow(srv_intro)

        self.le_host = QLineEdit("127.0.0.1")
        self.sp_port = QSpinBox()
        self.sp_port.setRange(1, 65535)
        self.sp_port.setValue(11434)
        form.addRow("Host", self.le_host)
        form.addRow("Port", self.sp_port)

        row = QHBoxLayout()
        self.btn_start = QPushButton("Start API")
        self.btn_start.setToolTip("Launch the local HTTP server on host/port below.")
        self.btn_stop = QPushButton("Stop API")
        self.btn_stop.setProperty("class", "danger")
        self.btn_stop.setToolTip("Stop the server process started from this window.")
        self.btn_start.clicked.connect(self._start_server)
        self.btn_stop.clicked.connect(self._stop_server)
        row.addWidget(self.btn_start)
        row.addWidget(self.btn_stop)
        form.addRow(row)

        self.lbl_srv_status = QLabel("API: not running")
        form.addRow(self.lbl_srv_status)

        reg = load_registry()
        self.le_host.setText(reg.settings.server_host)
        self.sp_port.setValue(reg.settings.server_port)
        self.le_host.editingFinished.connect(self._persist_server_registry)
        self.sp_port.editingFinished.connect(self._persist_server_registry)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(w)
        return scroll

    def _server_cmd(self) -> list[str]:
        host = self.le_host.text().strip()
        port = str(self.sp_port.value())
        srv = portable_server_exe()
        if srv is not None:
            return [
                str(srv),
                "serve",
                "--host",
                host,
                "--port",
                port,
                "--log-level",
                "info",
            ]
        return [
            sys.executable,
            "-m",
            "uvicorn",
            "omega_studio.server.app:app",
            "--host",
            host,
            "--port",
            port,
            "--log-level",
            "info",
        ]

    def _start_server(self) -> None:
        if self._server_proc and self._server_proc.poll() is None:
            QMessageBox.warning(self, "API", "The local API is already running.")
            return
        gs = load_gui_settings()
        env = gui_settings_to_child_env(os.environ.copy(), gs)
        env["OMEGA_STUDIO_HOST"] = self.le_host.text().strip()
        env["OMEGA_STUDIO_PORT"] = str(self.sp_port.value())
        br = Path(gs.omega_bundle_root.strip()) if gs.omega_bundle_root.strip() else bundle_root()
        env.setdefault("OMEGA_BUNDLE_ROOT", str(br.resolve()))
        if gs.prefer_vulkan_llama:
            vk = _vendor_llamacpp_vulkan_bin(br)
            if vk and vk.is_dir():
                key = str(vk.resolve())
                path = env.get("PATH", "")
                if key not in path.split(os.pathsep):
                    env["PATH"] = key + os.pathsep + path
        try:
            self._server_proc = subprocess.Popen(
                self._server_cmd(),
                cwd=str(br) if br.is_dir() else None,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                text=True,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Start failed", str(exc))
            return
        self.lbl_srv_status.setText(f"API: running · pid {self._server_proc.pid}")
        reg = load_registry()
        reg.settings.server_host = self.le_host.text().strip()
        reg.settings.server_port = int(self.sp_port.value())
        save_registry(reg)
        self._poll_timer.start(4000)
        self._sync_status_bar()
        self._update_header_status()
        self._update_dashboard_status()
        self._toasts.show("Server started", level="success")

        # Start log reader
        if self._server_proc.stdout is not None:
            self._log_line(f"Server started (pid {self._server_proc.pid})", tag="SERVER")
            self._log_reader = LogReaderWorker(self._server_proc.stdout)
            self._log_reader.line.connect(self._on_server_log_line)
            self._log_reader.finished.connect(self._log_reader.deleteLater)
            self._log_reader.start()

    def _on_server_log_line(self, line: str) -> None:
        self._log_line(line, tag="SERVER")

    def _stop_server(self, *, quiet: bool = False) -> None:
        if self._server_proc and self._server_proc.poll() is None:
            self._server_proc.terminate()
            try:
                self._server_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._server_proc.kill()
                self._server_proc.wait(timeout=2)
            self._server_proc = None
        if self._log_reader is not None:
            self._log_reader.stop()
            self._log_reader.wait(1000)
            self._log_reader = None
        self.lbl_srv_status.setText("API: not running")
        self._poll_timer.stop()
        self._sync_status_bar()
        self._update_header_status()
        self._update_dashboard_status()
        self._log_line("Server stopped", tag="SERVER")
        if not quiet:
            self._toasts.show("Server stopped", level="info")

    def _refresh_models_if_alive(self) -> None:
        if self._server_proc and self._server_proc.poll() is None:
            self._refresh_models_table()

    # --- Logs ---
    def _build_logs(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)
        lay.setContentsMargins(20, 20, 20, 20)

        intro = QLabel(
            "Live server output and API calls. Start the API to see logs here. "
            "Logs are not persisted to disk."
        )
        intro.setWordWrap(True)
        intro.setProperty("class", "muted")
        lay.addWidget(intro)

        ctrl = QHBoxLayout()
        self._chk_log_autoscroll = QCheckBox("Auto-scroll")
        self._chk_log_autoscroll.setChecked(True)
        btn_clear = QPushButton("Clear")
        btn_clear.setProperty("class", "tool")
        btn_clear.clicked.connect(self._clear_logs)
        ctrl.addWidget(self._chk_log_autoscroll)
        ctrl.addWidget(btn_clear)
        ctrl.addStretch()

        # Tag filter buttons
        self._log_filter_buttons: dict[str, QPushButton] = {}
        for tag in ("ALL", "SERVER", "API"):
            btn = QPushButton(tag)
            btn.setProperty("class", "preset-active" if tag == "ALL" else "preset")
            btn.setCheckable(False)
            btn.clicked.connect(lambda _c=False, t=tag: self._set_log_filter(t))
            self._log_filter_buttons[tag] = btn
            ctrl.addWidget(btn)
        self._log_filter_current = "ALL"

        lay.addLayout(ctrl)

        self._log_buffer: list[tuple[str, str]] = []
        self._txt_logs = QPlainTextEdit()
        self._txt_logs.setProperty("class", "log-viewer")
        self._txt_logs.setReadOnly(True)
        self._txt_logs.setPlaceholderText("Logs appear here once the server starts…")
        self._txt_logs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lay.addWidget(self._txt_logs, stretch=1)
        return w

    def _set_log_filter(self, tag: str) -> None:
        self._log_filter_current = tag
        for t, btn in self._log_filter_buttons.items():
            active = t == tag
            btn.setProperty("class", "preset-active" if active else "preset")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn.update()
        self._apply_log_filter()

    def _clear_logs(self) -> None:
        self._log_buffer.clear()
        self._txt_logs.clear()

    def closeEvent(self, event) -> None:
        self._shutting_down = True
        self._persist_window_and_session()
        # Cancel any running workers gently
        if (
            self._playground._stream_worker is not None
            and self._playground._stream_worker.isRunning()
        ):
            self._playground._stream_worker.abort()
            self._playground._stream_worker.wait(2000)
        if self._hf_worker is not None and self._hf_worker.isRunning():
            self._hf_worker.terminate()
            self._hf_worker.wait(2000)
        if self._url_worker is not None and self._url_worker.isRunning():
            self._url_worker.terminate()
            self._url_worker.wait(2000)
        self._stop_server(quiet=True)
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    apply_studio_theme(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
