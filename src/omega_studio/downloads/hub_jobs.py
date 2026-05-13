"""In-memory HF download jobs with ring-buffer progress for operator polling."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Deque, Literal

from omega_studio.paths import app_data_dir

JobStatus = Literal["queued", "running", "done", "error"]


# Rate is averaged across this window (seconds) so brief slow-spots /
# burst chunks don't dominate the displayed download speed. 1.5s
# matches what feels responsive in a polling UI without producing
# wild jumps from per-chunk timing variance.
_RATE_WINDOW_S = 1.5


@dataclass
class HubDownloadJob:
    job_id: str
    status: JobStatus = "queued"
    progress: float = 0.0
    message: str = ""
    result_path: str | None = None
    error: str | None = None
    bytes_done: int = 0
    bytes_total: int = 0
    rate_mbps: float = 0.0
    eta_seconds: float | None = None
    events: Deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=64))
    # Internal state for rate-window tracking — operators never see
    # these via to_public(); they're just bookkeeping for the EMA.
    _rate_window_start: float = 0.0
    _rate_window_start_bytes: int = 0

    def push(self, *, pct: float | None = None, msg: str = "") -> None:
        if pct is not None:
            self.progress = max(0.0, min(1.0, float(pct)))
        if msg:
            self.message = msg
        self.events.append({"progress": self.progress, "message": msg or self.message})

    def push_bytes(self, done: int, total: int, *, msg: str = "") -> None:
        """Update byte-level progress + recompute rate/ETA. Rate is
        averaged over ``_RATE_WINDOW_S`` so polling clients see a
        stable number, not per-chunk jitter."""
        done = max(0, int(done))
        total = max(0, int(total))
        self.bytes_done = done
        self.bytes_total = total
        if total > 0:
            self.progress = min(1.0, done / total)
        now = time.monotonic()
        if self._rate_window_start <= 0:
            self._rate_window_start = now
            self._rate_window_start_bytes = done
        elapsed = now - self._rate_window_start
        if elapsed >= _RATE_WINDOW_S:
            delta_bytes = max(0, done - self._rate_window_start_bytes)
            if elapsed > 0 and delta_bytes > 0:
                rate_bps = delta_bytes / elapsed
                self.rate_mbps = round(rate_bps / (1024 * 1024), 3)
                if total > done and rate_bps > 0:
                    self.eta_seconds = round((total - done) / rate_bps, 1)
                else:
                    self.eta_seconds = 0.0
            elif total > 0 and done >= total:
                self.eta_seconds = 0.0
            # Slide the window
            self._rate_window_start = now
            self._rate_window_start_bytes = done
        if msg:
            self.message = msg
        # Events are sampled — appending every chunk would saturate a
        # 64-deep deque on the first ~64MB. Only append when % moves
        # by at least 1 point OR message changes.
        if not self.events:
            self.events.append(
                {
                    "progress": self.progress,
                    "bytes_done": done,
                    "bytes_total": total,
                    "rate_mbps": self.rate_mbps,
                    "message": msg or self.message,
                }
            )
        else:
            last = self.events[-1]
            pct_delta = abs(self.progress - float(last.get("progress") or 0.0))
            msg_changed = bool(msg) and msg != last.get("message")
            if pct_delta >= 0.01 or msg_changed:
                self.events.append(
                    {
                        "progress": self.progress,
                        "bytes_done": done,
                        "bytes_total": total,
                        "rate_mbps": self.rate_mbps,
                        "message": msg or self.message,
                    }
                )

    def to_public(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "result_path": self.result_path,
            "error": self.error,
            "bytes_done": self.bytes_done,
            "bytes_total": self.bytes_total,
            "rate_mbps": self.rate_mbps,
            "eta_seconds": self.eta_seconds,
            "recent_events": list(self.events),
        }


class HubJobStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, HubDownloadJob] = {}
        self._db_path = db_path or (app_data_dir() / "hub_jobs.sqlite")
        self._init_db()
        self._load_existing()

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(self._db_path))

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS hub_jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    progress REAL NOT NULL,
                    message TEXT NOT NULL,
                    result_path TEXT,
                    error TEXT,
                    events_json TEXT NOT NULL
                )
                """
            )

    def _load_existing(self) -> None:
        with self._connect() as con:
            rows = con.execute(
                "SELECT job_id, status, progress, message, result_path, error, "
                "events_json FROM hub_jobs"
            ).fetchall()
        with self._lock:
            for job_id, status, progress, message, result_path, error, events_json in rows:
                try:
                    events = json.loads(events_json or "[]")
                except json.JSONDecodeError:
                    events = []
                self._jobs[job_id] = HubDownloadJob(
                    job_id=job_id,
                    status=status,
                    progress=float(progress),
                    message=message,
                    result_path=result_path,
                    error=error,
                    events=deque(events, maxlen=64),
                )

    def save(self, job: HubDownloadJob) -> None:
        payload = (
            job.job_id,
            job.status,
            float(job.progress),
            job.message,
            job.result_path,
            job.error,
            json.dumps(list(job.events)),
        )
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO hub_jobs (
                    job_id, status, progress, message, result_path, error, events_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status = excluded.status,
                    progress = excluded.progress,
                    message = excluded.message,
                    result_path = excluded.result_path,
                    error = excluded.error,
                    events_json = excluded.events_json
                """,
                payload,
            )

    def create(self) -> HubDownloadJob:
        jid = str(uuid.uuid4())
        job = HubDownloadJob(job_id=jid)
        with self._lock:
            self._jobs[jid] = job
        self.save(job)
        return job

    def get(self, job_id: str) -> HubDownloadJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def run_hf_download(
        self,
        job: HubDownloadJob,
        *,
        repo_id: str,
        filename: str,
        dest_dir: Path,
        download_fn: Callable[..., Path],
    ) -> None:
        def _cb(p: float) -> None:
            job.push(pct=p, msg="downloading")

        def _cb_bytes(done: int, total: int) -> None:
            job.push_bytes(done, total, msg="downloading")

        def _work() -> None:
            try:
                job.status = "running"
                job.push(pct=0.0, msg="started")
                self.save(job)
                path = download_fn(
                    repo_id,
                    filename,
                    dest_dir,
                    progress=_cb,
                    progress_bytes=_cb_bytes,
                )
                job.result_path = str(path)
                job.status = "done"
                job.push(pct=1.0, msg="complete")
                self.save(job)
            except Exception as exc:
                job.status = "error"
                job.error = str(exc)
                job.push(msg=f"error: {exc}")
                self.save(job)

        threading.Thread(target=_work, daemon=True).start()

    async def run_hf_download_async(
        self,
        job: HubDownloadJob,
        *,
        repo_id: str,
        filename: str,
        dest_dir: Path,
        download_fn: Callable[..., Path],
    ) -> None:
        loop = asyncio.get_running_loop()

        def _cb(p: float) -> None:
            loop.call_soon_threadsafe(lambda: job.push(pct=p, msg="downloading"))

        def _cb_bytes(done: int, total: int) -> None:
            loop.call_soon_threadsafe(
                lambda d=done, t=total: job.push_bytes(d, t, msg="downloading")
            )

        def _work() -> None:
            try:
                job.status = "running"
                loop.call_soon_threadsafe(lambda: job.push(pct=0.0, msg="started"))
                self.save(job)
                path = download_fn(
                    repo_id,
                    filename,
                    dest_dir,
                    progress=_cb,
                    progress_bytes=_cb_bytes,
                )
                job.result_path = str(path)
                job.status = "done"
                loop.call_soon_threadsafe(lambda: job.push(pct=1.0, msg="complete"))
                self.save(job)
            except Exception as exc:
                err = str(exc)
                job.status = "error"
                job.error = err
                loop.call_soon_threadsafe(lambda e=err: job.push(msg=f"error: {e}"))
                self.save(job)

        await asyncio.to_thread(_work)
