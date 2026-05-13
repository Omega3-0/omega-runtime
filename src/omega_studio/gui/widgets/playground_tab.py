"""Streaming chat playground for local ``/v1/chat/completions``."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from omega_studio.gui.workers import ChatStreamWorker
from omega_studio.gui_settings_store import load_gui_settings
from omega_studio.inference.thinking_parser import parse_thinking
from omega_studio.registry import load_registry


class PlaygroundTab(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        # --- Top controls ---
        top = QHBoxLayout()
        top.setSpacing(10)

        model_lay = QFormLayout()
        model_lay.setSpacing(6)
        model_lay.setContentsMargins(0, 0, 0, 0)
        self.cb_model = QComboBox()
        self.cb_model.setEditable(True)
        self.cb_model.setMinimumWidth(220)
        _le_model = self.cb_model.lineEdit()
        if _le_model is not None:
            _le_model.setPlaceholderText("Pick or type a model id")
        model_lay.addRow("Model", self.cb_model)

        self.sp_max_tokens = QSpinBox()
        self.sp_max_tokens.setRange(1, 131072)
        self.sp_max_tokens.setValue(256)
        model_lay.addRow("max_tokens", self.sp_max_tokens)

        top.addLayout(model_lay)
        top.addStretch()

        self.chk_show_thinking = QCheckBox("Show thinking")
        self.chk_show_thinking.setChecked(True)
        self.chk_show_thinking.setToolTip("Reveal reasoning blocks when the model emits them")
        top.addWidget(self.chk_show_thinking)

        self.btn_new_chat = QPushButton("New chat")
        self.btn_new_chat.setProperty("class", "tool")
        self.btn_new_chat.setToolTip("Start a fresh conversation")
        self.btn_new_chat.clicked.connect(self._new_chat)
        top.addWidget(self.btn_new_chat)

        self.btn_reload_models = QPushButton("Refresh")
        self.btn_reload_models.setProperty("class", "tool")
        self.btn_reload_models.setToolTip("Reload model names from the registry")
        self.btn_reload_models.clicked.connect(self._reload_models)
        top.addWidget(self.btn_reload_models)

        lay.addLayout(top)

        # --- System prompt (compact, expandable feel via max height) ---
        sys_row = QHBoxLayout()
        sys_row.setSpacing(6)
        sys_lbl = QLabel("System")
        sys_lbl.setStyleSheet("font-weight:600;color:#9aa3b8;font-size:12px;")
        sys_row.addWidget(sys_lbl)
        self.txt_system = QPlainTextEdit()
        self.txt_system.setPlaceholderText(
            "Optional system prompt (e.g. You are a helpful coding assistant…)"
        )
        self.txt_system.setMaximumHeight(50)
        self.txt_system.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        sys_row.addWidget(self.txt_system, stretch=1)
        lay.addLayout(sys_row)

        divider = QFrame()
        divider.setProperty("class", "divider")
        divider.setFixedHeight(1)
        lay.addWidget(divider)

        # --- Chat history ---
        self.txt_chat = QTextEdit()
        self.txt_chat.setProperty("class", "chat-history")
        self.txt_chat.setReadOnly(True)
        self.txt_chat.setPlaceholderText(
            "Chat history appears here. Choose a model, type a message, "
            "and press Send (Ctrl+Enter)."
        )
        self.txt_chat.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lay.addWidget(self.txt_chat, stretch=1)

        # --- Thinking block ---
        self.lbl_thinking = QLabel("Thinking / reasoning")
        self.lbl_thinking.setStyleSheet("color:#9aa3b8;font-weight:600;font-size:12px;")
        lay.addWidget(self.lbl_thinking)
        self.txt_thinking = QPlainTextEdit()
        self.txt_thinking.setReadOnly(True)
        self.txt_thinking.setPlaceholderText("Thinking block appears here when present")
        self.txt_thinking.setMaximumHeight(100)
        lay.addWidget(self.txt_thinking)

        # --- Input row ---
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.txt_user = QPlainTextEdit()
        self.txt_user.setPlaceholderText("Message…")
        self.txt_user.setMaximumHeight(80)
        self.txt_user.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bottom.addWidget(self.txt_user, stretch=1)

        btn_col = QVBoxLayout()
        btn_col.setSpacing(6)
        self.btn_send = QPushButton("Send")
        self.btn_send.setToolTip("Send chat (Ctrl+Enter)")
        self.btn_send.clicked.connect(self._on_send)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setProperty("class", "danger")
        self.btn_stop.setToolTip("Abort the current generation")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)
        btn_col.addWidget(self.btn_send)
        btn_col.addWidget(self.btn_stop)
        bottom.addLayout(btn_col)
        lay.addLayout(bottom)

        self._stream_worker: ChatStreamWorker | None = None
        self._raw_response = ""
        self._messages: list[dict[str, str]] = []
        self._streaming = False
        self._MAX_MESSAGES = 40

        send_sc = QShortcut(QKeySequence("Ctrl+Return"), self)
        send_sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        send_sc.activated.connect(self._on_send)

        self.cb_model.currentTextChanged.connect(self._sync_max_tokens_from_registry)

    def _set_chat_controls_busy(self, busy: bool) -> None:
        self.btn_send.setEnabled(not busy)
        self.btn_stop.setEnabled(busy)
        self.btn_reload_models.setEnabled(not busy)
        self.btn_new_chat.setEnabled(not busy)
        self.cb_model.setEnabled(not busy)
        self.txt_user.setEnabled(not busy)

    def _new_chat(self) -> None:
        self._messages.clear()
        self.txt_chat.clear()
        self.txt_thinking.clear()
        self._raw_response = ""

    def _render_conversation(self) -> None:
        """Rebuild the full chat HTML from _messages."""
        parts: list[str] = []
        for msg in self._messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                parts.append(
                    f'<div style="margin:10px 0;padding:10px 14px;background:#1b1f28;'
                    f'border-radius:8px;border-left:3px solid #3db8c6;">'
                    f'<div style="color:#3db8c6;font-weight:700;font-size:12px;">'
                    f'You</div>'
                    f'<div style="color:#e8eaf0;">{self._esc_html(content)}</div>'
                    f'</div>'
                )
            elif role == "assistant":
                parsed = parse_thinking(content)
                visible = parsed.visible.strip() or content.strip()
                parts.append(
                    f'<div style="margin:10px 0;padding:10px 14px;background:#1b1f28;'
                    f'border-radius:8px;border-left:3px solid #4ade80;">'
                    f'<div style="color:#4ade80;font-weight:700;font-size:12px;margin-bottom:4px;">'
                    f'Assistant</div>'
                    f'<div style="color:#e8eaf0;">{self._esc_html(visible)}</div>'
                    f'</div>'
                )
        self.txt_chat.setHtml("".join(parts))
        self.txt_chat.moveCursor(self.txt_chat.textCursor().MoveOperation.End)

    def _append_streaming_assistant_start(self) -> None:
        html = (
            '<div style="margin:10px 0;padding:10px 14px;background:#1b1f28;'
            'border-radius:8px;border-left:3px solid #4ade80;">'
            '<div style="color:#4ade80;font-weight:700;font-size:12px;margin-bottom:4px;">'
            'Assistant</div>'
            '<div style="color:#e8eaf0;">'
        )
        self.txt_chat.moveCursor(self.txt_chat.textCursor().MoveOperation.End)
        self.txt_chat.insertHtml(html)
        self.txt_chat.ensureCursorVisible()

    def _append_streaming_chunk(self, text: str) -> None:
        self.txt_chat.moveCursor(self.txt_chat.textCursor().MoveOperation.End)
        self.txt_chat.insertPlainText(text)
        self.txt_chat.ensureCursorVisible()

    def _end_streaming_html(self) -> None:
        self.txt_chat.moveCursor(self.txt_chat.textCursor().MoveOperation.End)
        self.txt_chat.insertHtml("</div></div>")
        self.txt_chat.ensureCursorVisible()

    @staticmethod
    def _esc_html(s: str) -> str:
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    def _reload_models(self) -> None:
        self.cb_model.clear()
        reg = load_registry()
        for mid in sorted(reg.models.keys()):
            self.cb_model.addItem(mid)

    def _sync_max_tokens_from_registry(self, mid: str) -> None:
        mid = mid.strip()
        if not mid:
            return
        reg = load_registry()
        rec = reg.models.get(mid)
        if not rec:
            return
        ui = rec.ui_overrides or {}
        if "max_tokens" in ui:
            self.sp_max_tokens.setValue(int(ui["max_tokens"]))

    def _base_url(self) -> str:
        reg = load_registry()
        h = reg.settings.server_host
        p = reg.settings.server_port
        return f"http://{h}:{p}"

    def _headers(self) -> dict[str, str]:
        gs = load_gui_settings()
        k = (gs.omega_api_key or "").strip()
        if k:
            return {"Authorization": f"Bearer {k}"}
        return {}

    def _build_messages(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        sys_text = self.txt_system.toPlainText().strip()
        if sys_text:
            out.append({"role": "system", "content": sys_text})
        out.extend(self._messages)
        return out

    def _on_send(self) -> None:
        if self._stream_worker is not None and self._stream_worker.isRunning():
            return
        mid = self.cb_model.currentText().strip()
        if not mid:
            QMessageBox.warning(self, "Playground", "Select or enter a model id.")
            return
        user_text = self.txt_user.toPlainText().strip()
        if not user_text:
            QMessageBox.warning(self, "Playground", "Enter a message.")
            return

        # Trim history if needed (keep most recent turns)
        while len(self._messages) >= self._MAX_MESSAGES:
            self._messages.pop(0)

        # Commit user message to history and render it
        self._messages.append({"role": "user", "content": user_text})
        self._render_conversation()
        self.txt_user.setPlainText("")
        self.txt_thinking.setPlainText("")
        self._raw_response = ""
        self._streaming = True
        self._set_chat_controls_busy(True)

        url = f"{self._base_url()}/v1/chat/completions"
        payload = {
            "model": mid,
            "messages": self._build_messages(),
            "max_tokens": int(self.sp_max_tokens.value()),
        }

        self._append_streaming_assistant_start()

        worker = ChatStreamWorker(url, payload, self._headers())
        self._stream_worker = worker
        worker.chunk.connect(self._on_chunk)
        worker.finished_ok.connect(self._on_stream_finished)
        worker.http_error.connect(self._on_stream_http_error)
        worker.failed.connect(self._on_stream_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_stop(self) -> None:
        if self._stream_worker is not None and self._stream_worker.isRunning():
            self._stream_worker.abort()
        self._streaming = False
        self._set_chat_controls_busy(False)
        self._end_streaming_html()

    def _on_chunk(self, text: str) -> None:
        self._raw_response += text
        self._append_streaming_chunk(text)

    def _on_stream_finished(self, full_text: str) -> None:
        self._streaming = False
        self._set_chat_controls_busy(False)
        self._raw_response = full_text
        self._messages.append({"role": "assistant", "content": full_text})
        parsed = parse_thinking(full_text)
        if self.chk_show_thinking.isChecked():
            self.txt_thinking.setPlainText(parsed.thinking_block or "")
        else:
            self.txt_thinking.setPlainText("")
        # Re-render cleanly so the conversation looks perfect
        self._render_conversation()
        self._stream_worker = None

    def _on_stream_http_error(self, code: int, body: str) -> None:
        self._streaming = False
        self._set_chat_controls_busy(False)
        self._end_streaming_html()
        self._messages.append({"role": "assistant", "content": f"[HTTP {code}]"})
        self._render_conversation()
        QMessageBox.critical(self, "API error", f"{code}\n{body[:2000]}")
        self._stream_worker = None

    def _on_stream_failed(self, err: str) -> None:
        self._streaming = False
        self._set_chat_controls_busy(False)
        self._end_streaming_html()
        self._messages.append({"role": "assistant", "content": f"[Error: {err}]"})
        self._render_conversation()
        QMessageBox.critical(self, "Request failed", err)
        self._stream_worker = None
