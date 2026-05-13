"""#580 — graceful shutdown with in-flight drain."""

from __future__ import annotations

import asyncio
import importlib
import time

import pytest
from starlette.testclient import TestClient

from omega_studio.config import ModelRecord, RegistryFile, StudioSettings
from omega_studio.server.drain import InFlightTracker, drain_on_shutdown


def _stub_registry() -> RegistryFile:
    return RegistryFile(
        version=1,
        model_folders=[],
        models={"stub-model": ModelRecord(path=r"C:\fake\stub.gguf", format="gguf")},
        settings=StudioSettings(),
    )


@pytest.fixture
def patched_registry(monkeypatch):
    app_mod = importlib.import_module("omega_studio.server.app")
    import omega_studio.server.routes_v1 as rv

    monkeypatch.setattr(app_mod, "load_registry", _stub_registry)
    monkeypatch.setattr(rv, "load_registry", _stub_registry)
    monkeypatch.setattr(rv, "apply_env_overrides", lambda r: r)


# ─────────────────────────────────────────────────────────────────
# InFlightTracker — core state machine
# ─────────────────────────────────────────────────────────────────


def test_tracker_starts_drained_and_count_zero():
    tracker = InFlightTracker()
    assert tracker.count == 0
    assert tracker.shutting_down is False


def test_tracker_acquire_release_increments_then_decrements():
    async def run():
        tracker = InFlightTracker()
        await tracker.acquire()
        assert tracker.count == 1
        await tracker.acquire()
        assert tracker.count == 2
        await tracker.release()
        assert tracker.count == 1
        await tracker.release()
        assert tracker.count == 0

    asyncio.run(run())


def test_tracker_release_clamped_at_zero():
    """Double-release shouldn't make count go negative — defends
    against a buggy middleware-finally that fires twice."""

    async def run():
        tracker = InFlightTracker()
        await tracker.release()  # before any acquire
        assert tracker.count == 0

    asyncio.run(run())


def test_wait_for_drain_returns_true_when_already_drained():
    async def run():
        tracker = InFlightTracker()
        ok = await tracker.wait_for_drain(timeout_s=1.0)
        assert ok is True

    asyncio.run(run())


def test_wait_for_drain_returns_true_when_request_completes_before_timeout():
    """Spawn a 'request' (acquire), schedule a release at 50ms,
    then wait_for_drain with 5s timeout — must return True."""

    async def run():
        tracker = InFlightTracker()
        await tracker.acquire()

        async def release_soon():
            await asyncio.sleep(0.05)
            await tracker.release()

        asyncio.create_task(release_soon())
        ok = await tracker.wait_for_drain(timeout_s=5.0)
        assert ok is True
        assert tracker.count == 0

    asyncio.run(run())


def test_wait_for_drain_returns_false_on_timeout():
    """Acquire and never release — wait_for_drain must return False
    after its timeout instead of blocking forever."""

    async def run():
        tracker = InFlightTracker()
        await tracker.acquire()
        ok = await tracker.wait_for_drain(timeout_s=0.05)
        assert ok is False
        assert tracker.count == 1

    asyncio.run(run())


def test_drain_on_shutdown_flips_flag_and_waits():
    """drain_on_shutdown sets shutting_down=True and returns once
    the tracker drains."""

    async def run():
        tracker = InFlightTracker()
        await tracker.acquire()

        async def release_after_short_delay():
            await asyncio.sleep(0.05)
            await tracker.release()

        asyncio.create_task(release_after_short_delay())
        start = time.monotonic()
        await drain_on_shutdown(tracker)
        elapsed = time.monotonic() - start
        assert tracker.shutting_down is True
        assert tracker.count == 0
        assert elapsed < 5.0

    asyncio.run(run())


def test_drain_on_shutdown_honors_timeout(monkeypatch):
    """If drain doesn't complete in time, drain_on_shutdown returns
    without blocking forever; the warning log surfaces the timeout
    but the function MUST exit so uvicorn can hard-stop."""
    monkeypatch.setenv("OMEGA_DRAIN_TIMEOUT_S", "0.1")

    async def run():
        tracker = InFlightTracker()
        await tracker.acquire()
        start = time.monotonic()
        await drain_on_shutdown(tracker)
        elapsed = time.monotonic() - start
        assert elapsed < 2.0, f"drain timeout did not bail; elapsed={elapsed:.2f}s"
        assert tracker.count == 1  # never drained — operator can see

    asyncio.run(run())


# ─────────────────────────────────────────────────────────────────
# Middleware integration
# ─────────────────────────────────────────────────────────────────


def test_inflight_count_decrements_on_normal_request(patched_registry, monkeypatch):
    """A successful request must release its slot when done — otherwise
    one bad codepath could pin the count and block future drains."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        # Health response goes through tracker too
        r = client.get("/health")
        assert r.status_code == 200
        # Count must be back to 0 after the request finishes
        tracker = client.app.state.inflight_tracker
        assert tracker.count == 0


def test_drain_state_rejects_v1_with_503(patched_registry, monkeypatch):
    """Once shutting_down flips, /v1/* must return 503 service_draining
    so a load balancer can drain too. Retry-After header included so
    clients back off."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        tracker = client.app.state.inflight_tracker
        tracker.shutting_down = True
        r = client.get("/v1/models")
    assert r.status_code == 503
    assert r.json()["detail"] == "service_draining"
    assert r.headers.get("retry-after") == "5"


def test_drain_state_keeps_health_open(patched_registry, monkeypatch):
    """During drain, /health stays 200 so monitoring + load balancers
    detect the shutting-down state. Closing /health during drain
    causes false 'unhealthy' alerts in many platforms."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        tracker = client.app.state.inflight_tracker
        tracker.shutting_down = True
        r = client.get("/health")
    assert r.status_code == 200


def test_drain_503_still_carries_x_request_id(patched_registry, monkeypatch):
    """request_id middleware is OUTERMOST — drain-rejected requests
    must STILL carry X-Request-ID for forensics. This is the whole
    reason request_id is declared after the tracker."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        tracker = client.app.state.inflight_tracker
        tracker.shutting_down = True
        r = client.get("/v1/models")
    assert r.status_code == 503
    assert "x-request-id" in r.headers


def test_drain_state_returns_to_normal_when_flag_cleared(patched_registry, monkeypatch):
    """Toggling shutting_down off lets traffic through again — the
    decision is per-request, not sticky."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        tracker = client.app.state.inflight_tracker
        tracker.shutting_down = True
        denied = client.get("/v1/models")
        assert denied.status_code == 503
        tracker.shutting_down = False
        ok = client.get("/v1/models")
        assert ok.status_code == 200


def test_drain_state_does_not_reject_paths_outside_v1_admin(patched_registry, monkeypatch):
    """Drain only gates /v1/* and /admin/*; other surfaces (root,
    docs, version helpers) stay open — they're cheap reads that don't
    hold engine slots."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        tracker = client.app.state.inflight_tracker
        tracker.shutting_down = True
        # /health is the canonical 'still up' surface during drain
        assert client.get("/health").status_code == 200
