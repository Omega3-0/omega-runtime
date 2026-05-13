"""Per-request X-Request-ID middleware + context propagation tests."""

from __future__ import annotations

import importlib
import logging
import re

import pytest
from starlette.testclient import TestClient

from omega_studio.config import ModelRecord, RegistryFile, StudioSettings
from omega_studio.server.request_context import (
    _sanitize_request_id,
    get_current_request_id,
    install_request_id_filter,
    new_request_id,
    reset_current_request_id,
    set_current_request_id,
)


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
# Unit tests for the context primitives
# ─────────────────────────────────────────────────────────────────


def test_new_request_id_returns_hex_uuid():
    rid = new_request_id()
    assert re.fullmatch(r"[0-9a-f]{32}", rid)


def test_contextvar_set_get_reset_cycle():
    assert get_current_request_id() == ""
    token = set_current_request_id("test-id-abc")
    try:
        assert get_current_request_id() == "test-id-abc"
    finally:
        reset_current_request_id(token)
    assert get_current_request_id() == ""


def test_sanitize_strips_unsafe_chars():
    assert _sanitize_request_id("abc-123_DEF") == "abc-123_DEF"
    # Newlines / spaces / quotes / nulls: stripped
    assert _sanitize_request_id("hostile\nvalue") == "hostilevalue"
    assert _sanitize_request_id("with spaces") == "withspaces"
    assert _sanitize_request_id("with\x00nul") == "withnul"
    assert _sanitize_request_id('"injection"') == "injection"


def test_sanitize_caps_length_at_128():
    long_id = "a" * 500
    cleaned = _sanitize_request_id(long_id)
    assert len(cleaned) == 128
    assert cleaned == "a" * 128


def test_sanitize_empty_and_whitespace_only_return_empty():
    assert _sanitize_request_id("") == ""
    assert _sanitize_request_id("   \t\n") == ""


# ─────────────────────────────────────────────────────────────────
# Middleware integration tests
# ─────────────────────────────────────────────────────────────────


def test_response_carries_x_request_id_header(patched_registry, monkeypatch):
    """Every response — including /health — gets an X-Request-ID
    header with a UUID-shaped value when no upstream header is sent."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        r = client.get("/health")
    assert r.status_code == 200
    rid = r.headers.get("x-request-id")
    assert rid is not None, "response must include X-Request-ID"
    assert re.fullmatch(r"[0-9a-f]{32}", rid), f"id should be UUID hex, got {rid!r}"


def test_upstream_x_request_id_is_respected(patched_registry, monkeypatch):
    """If an upstream proxy / client sets X-Request-ID, we propagate
    that value (after sanitizing) instead of generating fresh —
    enables end-to-end distributed tracing."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        r = client.get("/health", headers={"X-Request-ID": "trace-from-upstream"})
    assert r.status_code == 200
    assert r.headers.get("x-request-id") == "trace-from-upstream"


def test_upstream_x_request_id_sanitized(patched_registry, monkeypatch):
    """Hostile upstream IDs are sanitized — no log injection via
    newlines / null bytes / etc."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        r = client.get("/health", headers={"X-Request-ID": "evil\ninjection"})
    rid = r.headers.get("x-request-id")
    assert rid == "evilinjection"
    # And the newline never made it through
    assert "\n" not in rid


def test_each_request_gets_unique_id(patched_registry, monkeypatch):
    """Two requests in a row produce two different IDs when neither
    sets an upstream header."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        r1 = client.get("/health")
        r2 = client.get("/health")
    assert r1.headers["x-request-id"] != r2.headers["x-request-id"]


def test_auth_rejected_requests_still_get_request_id(patched_registry, monkeypatch):
    """Critical: requests rejected by the API-key gate (401/403) MUST
    still include X-Request-ID. Those are the ones operators most
    want to trace in forensic logs. Middleware ordering: request_id
    is declared LAST so it runs OUTERMOST (LIFO) — auth rejection
    happens inside the request_id middleware's scope."""
    monkeypatch.setenv("OMEGA_API_KEY", "test-key")
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        no_bearer = client.get("/v1/models")
        wrong_bearer = client.get("/v1/models", headers={"Authorization": "Bearer wrong"})
    assert no_bearer.status_code == 401
    assert wrong_bearer.status_code == 403
    assert "x-request-id" in no_bearer.headers
    assert "x-request-id" in wrong_bearer.headers


def test_request_id_filter_stamps_log_records():
    """The logging filter pulls the active request_id onto every
    LogRecord — verified by capturing records during a context-bound
    block and asserting record.request_id == the active id."""
    install_request_id_filter()
    lg = logging.getLogger("omega_studio")
    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            captured.append(record)

    handler = _Capture()
    handler.setLevel(logging.DEBUG)
    lg.addHandler(handler)
    lg.setLevel(logging.DEBUG)
    try:
        token = set_current_request_id("test-id-99")
        try:
            lg.info("inside request")
        finally:
            reset_current_request_id(token)
        lg.info("outside request")
    finally:
        lg.removeHandler(handler)

    inside = next(r for r in captured if r.getMessage() == "inside request")
    outside = next(r for r in captured if r.getMessage() == "outside request")
    assert inside.request_id == "test-id-99"
    assert outside.request_id == "-"  # default sentinel when no request bound
