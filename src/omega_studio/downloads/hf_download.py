"""Resume-capable downloads using huggingface_hub when possible."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable

log = logging.getLogger("omega_studio.downloads")


def download_hf_file(
    repo_id: str,
    filename: str,
    dest_dir: Path,
    *,
    progress: Callable[[float], None] | None = None,
    progress_bytes: Callable[[int, int], None] | None = None,
) -> Path:
    """Download a single file from a HF repo into dest_dir (resume supported).

    Two callback shapes are supported simultaneously:
      * ``progress(pct: float)`` — legacy, simple percentage 0-1
      * ``progress_bytes(done: int, total: int)`` — for rate / ETA UX

    When either is provided we route via the manual ``hf_hub_url`` +
    ``download_url_resume`` path so progress is visible. The bare
    ``hf_hub_download`` codepath is hash-checked but reports no
    progress (it uses tqdm bound to stderr), so it's only used when
    the caller doesn't care about progress reporting.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    token = (os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or "").strip()
    if progress or progress_bytes:
        try:
            from huggingface_hub import hf_hub_url
        except ImportError as exc:
            raise RuntimeError("huggingface_hub is required for HF downloads") from exc
        headers = {"Authorization": f"Bearer {token}"} if token else None
        url = hf_hub_url(repo_id=repo_id, filename=filename)
        dest = dest_dir / filename
        download_url_resume(
            url, dest,
            progress=progress,
            progress_bytes=progress_bytes,
            headers=headers,
        )
        return dest

    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise RuntimeError("huggingface_hub is required for HF downloads") from exc

    extra: dict = {}
    if token:
        extra["token"] = token

    path = hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        local_dir=str(dest_dir),
        **extra,
    )
    return Path(path)


def download_url_resume(
    url: str,
    dest: Path,
    *,
    progress: Callable[[float], None] | None = None,
    progress_bytes: Callable[[int, int], None] | None = None,
    headers: dict[str, str] | None = None,
):
    """Minimal urllib resume using Range when partial file exists.

    Calls both ``progress(pct)`` and ``progress_bytes(done, total)``
    when supplied — operators wanting just a percentage keep that
    callback; the hub-jobs path wires the byte form for rate / ETA.
    """
    import urllib.error
    import urllib.request

    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".partial")
    start = partial.stat().st_size if partial.is_file() else 0
    req = urllib.request.Request(url, headers=headers or {})
    if start > 0:
        req.add_header("Range", f"bytes={start}-")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length") or 0) + start
            mode = "ab" if start else "wb"
            done = start
            with open(partial, mode) as fh:
                block = 1024 * 1024
                while True:
                    chunk = resp.read(block)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    if progress and total:
                        progress(min(1.0, done / total))
                    if progress_bytes:
                        progress_bytes(done, total)
        partial.replace(dest)
        if progress:
            progress(1.0)
        if progress_bytes:
            progress_bytes(done, total or done)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and partial.is_file():
            partial.replace(dest)
            return
        log.exception("download failed")
        raise
