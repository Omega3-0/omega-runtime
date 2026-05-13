"""Background workers so network-heavy tasks do not freeze the Qt event loop."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
from PySide6.QtCore import QThread, Signal


class HfDownloadWorker(QThread):
    """Run ``download_hf_file`` off the GUI thread."""

    progress_ratio = Signal(float)  # 0.0–1.0
    succeeded = Signal(object)  # Path
    failed = Signal(str)

    def __init__(self, repo_id: str, filename: str, dest_dir: Path) -> None:
        super().__init__()
        self._repo_id = repo_id
        self._filename = filename
        self._dest_dir = dest_dir

    def run(self) -> None:  # pragma: no cover — Qt loop
        from omega_studio.downloads.hf_download import download_hf_file

        def on_progress(p: float) -> None:
            self.progress_ratio.emit(float(p))

        try:
            path = download_hf_file(
                self._repo_id,
                self._filename,
                self._dest_dir,
                progress=on_progress,
            )
            self.succeeded.emit(path)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class ChatCompletionWorker(QThread):
    """POST ``/v1/chat/completions`` off the GUI thread (long generations stay responsive)."""

    succeeded = Signal(object)  # parsed JSON dict
    http_error = Signal(int, str)  # status, body excerpt
    failed = Signal(str)

    def __init__(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        *,
        timeout_s: float = 300.0,
    ) -> None:
        super().__init__()
        self._url = url
        self._payload = payload
        self._headers = headers
        self._timeout_s = timeout_s

    def run(self) -> None:  # pragma: no cover — Qt loop
        try:
            r = httpx.post(
                self._url,
                json=self._payload,
                headers=self._headers,
                timeout=self._timeout_s,
            )
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        if r.status_code >= 400:
            self.http_error.emit(r.status_code, r.text[:12000])
            return
        try:
            data = r.json()
        except json.JSONDecodeError:
            self.failed.emit(f"Invalid JSON:\n{r.text[:2000]}")
            return
        if not isinstance(data, dict):
            self.failed.emit("Response JSON must be an object")
            return
        self.succeeded.emit(data)


class ChatStreamWorker(QThread):
    """Streaming POST ``/v1/chat/completions`` with SSE parsing."""

    chunk = Signal(str)  # delta content text
    finished_ok = Signal(str)  # full accumulated text
    http_error = Signal(int, str)
    failed = Signal(str)

    def __init__(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        *,
        timeout_s: float = 300.0,
    ) -> None:
        super().__init__()
        self._url = url
        self._payload = payload
        self._headers = headers
        self._timeout_s = timeout_s
        self._abort = False

    def abort(self) -> None:
        self._abort = True

    def run(self) -> None:  # pragma: no cover — Qt loop
        payload = dict(self._payload)
        payload["stream"] = True
        full_text = ""
        try:
            with httpx.stream(
                "POST",
                self._url,
                json=payload,
                headers=self._headers,
                timeout=self._timeout_s,
            ) as response:
                if response.status_code >= 400:
                    body = response.read()[:12000].decode("utf-8", errors="replace")
                    self.http_error.emit(response.status_code, body)
                    return
                for line in response.iter_lines():
                    if self._abort:
                        break
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {} if isinstance(choices[0], dict) else {}
                    content = delta.get("content")
                    if isinstance(content, str):
                        full_text += content
                        self.chunk.emit(content)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.finished_ok.emit(full_text)


class UrlDownloadWorker(QThread):
    """Run ``download_url_resume`` off the GUI thread."""

    progress_ratio = Signal(float)
    succeeded = Signal(object)  # Path
    failed = Signal(str)

    def __init__(self, url: str, dest: Path) -> None:
        super().__init__()
        self._url = url
        self._dest = dest

    def run(self) -> None:  # pragma: no cover — Qt loop
        from omega_studio.downloads.hf_download import download_url_resume

        def on_progress(p: float) -> None:
            self.progress_ratio.emit(float(p))

        try:
            download_url_resume(self._url, self._dest, progress=on_progress)
            self.succeeded.emit(self._dest)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class LogReaderWorker(QThread):
    """Read lines from a text-mode pipe and emit them without blocking the GUI."""

    line = Signal(str)

    def __init__(self, pipe) -> None:
        super().__init__()
        self._pipe = pipe
        self._running = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:  # pragma: no cover — Qt loop
        try:
            for line in self._pipe:
                if not self._running:
                    break
                self.line.emit(line.rstrip("\n\r"))
        except Exception:
            pass


class ModelSyncWorker(QThread):
    """GET ``/v1/models`` off the GUI thread so the UI never freezes."""

    succeeded = Signal(object)  # {"api_ok": bool, "api_by_id": dict, "err_note": str}
    failed = Signal(str)

    def __init__(
        self,
        url: str,
        headers: dict[str, str],
        *,
        timeout_s: float = 5.0,
    ) -> None:
        super().__init__()
        self._url = url
        self._headers = headers
        self._timeout_s = timeout_s

    def run(self) -> None:  # pragma: no cover — Qt loop
        api_ok = False
        api_by_id: dict[str, dict[str, Any]] = {}
        err_note = ""
        try:
            r = httpx.get(self._url, headers=self._headers, timeout=self._timeout_s)
            if r.status_code == 200:
                api_ok = True
                payload = r.json()
                for item in payload.get("data") or []:
                    oid = item.get("id") if isinstance(item, dict) else None
                    if oid:
                        api_by_id[str(oid)] = item
            else:
                err_note = f"HTTP {r.status_code}"
        except Exception as exc:  # noqa: BLE001
            err_note = str(exc)[:120]
        self.succeeded.emit({"api_ok": api_ok, "api_by_id": api_by_id, "err_note": err_note})


class ServerPatchWorker(QThread):
    """PATCH ``/v1/studio/models/{mid}`` off the GUI thread."""

    succeeded = Signal(int)  # status code
    http_error = Signal(int, str)
    failed = Signal(str)

    def __init__(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        *,
        timeout_s: float = 5.0,
    ) -> None:
        super().__init__()
        self._url = url
        self._payload = payload
        self._headers = headers
        self._timeout_s = timeout_s

    def run(self) -> None:  # pragma: no cover — Qt loop
        try:
            r = httpx.patch(
                self._url,
                json=self._payload,
                headers=self._headers,
                timeout=self._timeout_s,
            )
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        if r.status_code >= 400:
            self.http_error.emit(r.status_code, r.text[:500])
            return
        self.succeeded.emit(r.status_code)
