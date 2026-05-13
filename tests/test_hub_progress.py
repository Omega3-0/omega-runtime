"""#582 — Hub download real progress reporting (bytes / rate / ETA)."""

from __future__ import annotations

import sys
import time
import types
from pathlib import Path
from typing import Any

from omega_studio.downloads.hf_download import download_hf_file
from omega_studio.downloads.hub_jobs import HubDownloadJob, HubJobStore

# ─────────────────────────────────────────────────────────────────
# HubDownloadJob.push_bytes — byte-level state tracking
# ─────────────────────────────────────────────────────────────────


def test_push_bytes_sets_byte_counts_and_progress():
    """Byte updates compute progress AND populate bytes_done/total
    so the operator UI doesn't have to guess."""
    job = HubDownloadJob(job_id="t1")
    job.push_bytes(0, 1000)
    assert job.bytes_done == 0
    assert job.bytes_total == 1000
    assert job.progress == 0.0

    job.push_bytes(500, 1000)
    assert job.bytes_done == 500
    assert job.bytes_total == 1000
    assert job.progress == 0.5

    job.push_bytes(1000, 1000)
    assert job.progress == 1.0


def test_push_bytes_clamps_progress_to_one():
    """Defensive: if upstream reports done > total (shouldn't happen
    but Range/redirect interactions can occasionally lie), progress
    stays bounded at 1.0."""
    job = HubDownloadJob(job_id="t2")
    job.push_bytes(2000, 1000)
    assert job.progress == 1.0


def test_push_bytes_rate_computed_after_window(monkeypatch):
    """rate_mbps populates after the rate-averaging window slides —
    catches the previously-silent case where operators saw 0 MB/s
    even on multi-minute downloads."""
    job = HubDownloadJob(job_id="t3")
    # Freeze the clock so we control window advancement.
    clock = [1000.0]
    monkeypatch.setattr(
        "omega_studio.downloads.hub_jobs.time.monotonic",
        lambda: clock[0],
    )
    # First sample seeds the window
    job.push_bytes(0, 1024 * 1024 * 100)  # 0 / 100 MB
    assert job.rate_mbps == 0.0
    # Within the window — rate not yet recomputed
    clock[0] += 0.5
    job.push_bytes(1024 * 1024, 1024 * 1024 * 100)  # 1 MB
    assert job.rate_mbps == 0.0
    # Past the window — rate should compute. 50 MB in 1.5s window = ~33 MB/s
    clock[0] += 1.5
    job.push_bytes(1024 * 1024 * 50, 1024 * 1024 * 100)
    assert job.rate_mbps > 0
    assert 20 < job.rate_mbps < 60, f"unrealistic rate computed: {job.rate_mbps}"


def test_push_bytes_eta_seconds_populated_when_rate_known(monkeypatch):
    """Once rate is known, ETA = (total - done) / rate — operator
    can plan around it."""
    job = HubDownloadJob(job_id="t4")
    clock = [1000.0]
    monkeypatch.setattr(
        "omega_studio.downloads.hub_jobs.time.monotonic",
        lambda: clock[0],
    )
    # Seed
    job.push_bytes(0, 1024 * 1024 * 200)  # 200 MB total
    # Past window, 100 MB in 1.5s = ~66 MB/s
    clock[0] += 1.5
    job.push_bytes(1024 * 1024 * 100, 1024 * 1024 * 200)
    assert job.eta_seconds is not None
    assert job.eta_seconds > 0
    # 100 MB remaining at ~66 MB/s → ETA ~1.5s. Tolerant range.
    assert 0.5 < job.eta_seconds < 3.0, f"unexpected eta: {job.eta_seconds}"


def test_push_bytes_eta_zero_at_completion(monkeypatch):
    """When the download is done, eta should be 0 — not a stale
    estimate from before."""
    job = HubDownloadJob(job_id="t5")
    clock = [1000.0]
    monkeypatch.setattr(
        "omega_studio.downloads.hub_jobs.time.monotonic",
        lambda: clock[0],
    )
    job.push_bytes(0, 1000)
    clock[0] += 2.0
    job.push_bytes(1000, 1000)
    assert job.eta_seconds == 0.0


def test_push_bytes_events_sampled_not_per_chunk():
    """events deque must NOT fill with one entry per call — under a
    100k-chunk download the 64-deep ring would lose ALL real events.
    Only sample when progress moves >=1pp."""
    job = HubDownloadJob(job_id="t6")
    total = 100_000_000
    # Simulate 1000 chunks of 100KB each → 0.1% per chunk
    chunk = 100_000
    done = 0
    for _ in range(1000):
        done += chunk
        job.push_bytes(done, total)
    # Without sampling we'd get 1000 events; with 1pp sampling we
    # should get close to 100 (some headroom for the first entry).
    assert len(job.events) <= 100, f"events not sampled: {len(job.events)}"
    assert len(job.events) >= 50, f"events under-sampled: {len(job.events)}"


# ─────────────────────────────────────────────────────────────────
# to_public — operator-visible response shape
# ─────────────────────────────────────────────────────────────────


def test_to_public_includes_byte_level_fields(monkeypatch):
    """The status endpoint payload must expose bytes_done/total/rate/ETA
    so a polling client can render a real progress bar."""
    job = HubDownloadJob(job_id="t7")
    clock = [1000.0]
    monkeypatch.setattr(
        "omega_studio.downloads.hub_jobs.time.monotonic",
        lambda: clock[0],
    )
    job.push_bytes(0, 1024 * 1024 * 10)
    clock[0] += 2.0
    job.push_bytes(1024 * 1024 * 5, 1024 * 1024 * 10)
    pub = job.to_public()
    assert pub["bytes_done"] == 1024 * 1024 * 5
    assert pub["bytes_total"] == 1024 * 1024 * 10
    assert pub["progress"] == 0.5
    assert "rate_mbps" in pub
    assert "eta_seconds" in pub


# ─────────────────────────────────────────────────────────────────
# download_hf_file — new progress_bytes callback wired end-to-end
# ─────────────────────────────────────────────────────────────────


def test_download_hf_file_passes_progress_bytes_callback(monkeypatch, tmp_path: Path):
    """When progress_bytes is supplied, download_hf_file routes via
    the URL-resume path and forwards the callback through."""
    seen: list[tuple[int, int]] = []

    fake_mod = types.ModuleType("huggingface_hub")
    fake_mod.hf_hub_url = lambda **_kw: "https://huggingface.local/m.gguf"
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_mod)

    def fake_url_resume(url: str, dest: Path, **kwargs: Any) -> None:
        pb = kwargs.get("progress_bytes")
        assert pb is not None, "progress_bytes not forwarded to download_url_resume"
        pb(500, 1000)
        pb(1000, 1000)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("ok", encoding="utf-8")

    monkeypatch.setattr(
        "omega_studio.downloads.hf_download.download_url_resume",
        fake_url_resume,
    )

    got = download_hf_file(
        "org/repo",
        "m.gguf",
        tmp_path,
        progress_bytes=lambda d, t: seen.append((d, t)),
    )
    assert got == tmp_path / "m.gguf"
    assert seen == [(500, 1000), (1000, 1000)]


def test_download_hf_file_supports_both_callbacks_concurrently(monkeypatch, tmp_path: Path):
    """Some operators use the float callback for UI; the hub-jobs
    layer uses the byte callback for rate. Both should fire."""
    pcts: list[float] = []
    bytes_seen: list[tuple[int, int]] = []

    fake_mod = types.ModuleType("huggingface_hub")
    fake_mod.hf_hub_url = lambda **_kw: "https://huggingface.local/m.gguf"
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_mod)

    def fake_url_resume(url: str, dest: Path, **kwargs: Any) -> None:
        kwargs["progress"](0.25)
        kwargs["progress_bytes"](256, 1024)
        kwargs["progress"](1.0)
        kwargs["progress_bytes"](1024, 1024)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("ok", encoding="utf-8")

    monkeypatch.setattr(
        "omega_studio.downloads.hf_download.download_url_resume",
        fake_url_resume,
    )

    download_hf_file(
        "org/repo",
        "m.gguf",
        tmp_path,
        progress=pcts.append,
        progress_bytes=lambda d, t: bytes_seen.append((d, t)),
    )
    assert pcts == [0.25, 1.0]
    assert bytes_seen == [(256, 1024), (1024, 1024)]


# ─────────────────────────────────────────────────────────────────
# HubJobStore — async runner wires progress_bytes to push_bytes
# ─────────────────────────────────────────────────────────────────


def test_hub_job_store_runs_download_and_records_bytes(monkeypatch, tmp_path: Path):
    """End-to-end via sync runner: the byte callback reaches the job
    state and populates bytes_done/total."""
    store = HubJobStore(tmp_path / "hub.sqlite")
    job = store.create()
    completed = []

    def fake_download_fn(
        repo_id: str,
        filename: str,
        dest_dir: Path,
        *,
        progress=None,
        progress_bytes=None,
    ) -> Path:
        # Should always be called with both callbacks in this path
        assert progress is not None
        assert progress_bytes is not None
        progress_bytes(0, 2000)
        progress_bytes(1000, 2000)
        progress_bytes(2000, 2000)
        dest_path = dest_dir / filename
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_text("ok")
        completed.append(dest_path)
        return dest_path

    store.run_hf_download(
        job,
        repo_id="org/repo",
        filename="m.gguf",
        dest_dir=tmp_path / "models",
        download_fn=fake_download_fn,
    )

    deadline = time.time() + 3.0
    while time.time() < deadline:
        if job.status == "done":
            break
        time.sleep(0.02)
    assert job.status == "done", f"job did not complete; status={job.status}"
    assert job.bytes_done == 2000
    assert job.bytes_total == 2000
    assert job.progress == 1.0
