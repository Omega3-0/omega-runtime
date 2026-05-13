"""FastAPI application wiring."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware

from omega_studio import __version__
from omega_studio.downloads.hub_jobs import HubJobStore
from omega_studio.inference.backend_profile import refresh_backend_profile
from omega_studio.inference.backends import build_backend_snapshot, ensure_vendor_bin_on_path
from omega_studio.inference.engine import InferenceEngine
from omega_studio.registry import apply_env_overrides, load_registry
from omega_studio.resource_manager import ResourceManager
from omega_studio.server import routes_admin, routes_health, routes_hub, routes_v1
from omega_studio.server.drain import InFlightTracker, drain_on_shutdown
from omega_studio.server.request_context import (
    _sanitize_request_id,
    install_request_id_filter,
    new_request_id,
    reset_current_request_id,
    set_current_request_id,
)

log = logging.getLogger("omega_studio.server")


def _api_key_configured() -> str | None:
    key = os.environ.get("OMEGA_API_KEY", "").strip()
    return key or None


def _max_request_bytes() -> int:
    raw = os.environ.get("OMEGA_MAX_REQUEST_BYTES", "").strip()
    if not raw:
        return 10 * 1024 * 1024
    try:
        return max(0, int(raw))
    except ValueError:
        return 10 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_vendor_bin_on_path()
    try:
        refresh_backend_profile()
    except Exception:
        log.exception("backend profile refresh at startup failed")
    try:
        app.state.backend_snapshot = build_backend_snapshot()
    except Exception:
        log.exception("backend snapshot at startup failed")
        app.state.backend_snapshot = None
    reg = load_registry()
    runtime_reg = apply_env_overrides(reg)
    app.state.registry = reg
    app.state.engine = InferenceEngine()
    app.state.hub_jobs = HubJobStore()
    app.state.inflight_tracker = InFlightTracker()
    max_c = min(15, int(runtime_reg.settings.max_concurrent_models))
    app.state.resource_manager = ResourceManager(
        max_loaded=max_c,
        eviction_enabled=bool(runtime_reg.settings.lru_eviction_enabled),
    )
    for mid, rec in runtime_reg.models.items():
        app.state.resource_manager.set_pin(mid, rec.pinned)
    log.info(
        "Omega Runtime Studio API — models=%d max_loaded=%d",
        len(reg.models),
        max_c,
    )
    yield
    # Graceful drain: wait for in-flight requests to finish before
    # tearing down engine handles. Without this, mid-stream chats and
    # long embeddings get cancelled on shutdown and clients see
    # truncated SSE / broken JSON.
    tracker = getattr(app.state, "inflight_tracker", None)
    if isinstance(tracker, InFlightTracker):
        await drain_on_shutdown(tracker)
    eng = getattr(app.state, "engine", None)
    handles = getattr(eng, "_handles", None)
    if isinstance(handles, dict):
        handles.clear()


def create_app() -> FastAPI:
    app = FastAPI(title="Omega Runtime Studio", version=__version__, lifespan=lifespan)
    # Attach request_id filter to relevant loggers ONCE at app construction.
    # The filter reads from a contextvar so every log record emitted while
    # serving a request automatically carries the request_id.
    install_request_id_filter()
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=os.environ.get(
            "OMEGA_CORS_ALLOW_ORIGIN_REGEX",
            r"^http://(127\.0\.0\.1|localhost)(:\d+)?$",
        ),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def omega_request_size_gate(request: Request, call_next):
        max_bytes = _max_request_bytes()
        if max_bytes > 0:
            raw_len = request.headers.get("content-length")
            if raw_len:
                try:
                    if int(raw_len) > max_bytes:
                        return JSONResponse(
                            {"detail": "request_too_large"},
                            status_code=413,
                        )
                except ValueError:
                    pass
        return await call_next(request)

    @app.middleware("http")
    async def omega_api_key_gate(request: Request, call_next):
        expected = _api_key_configured()
        if not expected:
            return await call_next(request)
        # CORS preflight bypass: OPTIONS requests with an
        # `Access-Control-Request-Method` header are browser-issued
        # preflight checks. They DO NOT and CANNOT carry the bearer
        # token (CORS spec forbids credentials on preflight). If the
        # auth gate rejects them with 401, browsers can't talk to the
        # API at all when OMEGA_API_KEY is set. Let CORSMiddleware
        # (downstream in the chain) handle preflight; the actual
        # follow-up request will be auth-gated normally.
        if (
            request.method == "OPTIONS"
            and "access-control-request-method" in {k.lower() for k in request.headers.keys()}
        ):
            return await call_next(request)
        path = request.url.path or ""
        gated = path == "/v1" or path.startswith("/v1/")
        gated = gated or path == "/admin" or path.startswith("/admin/")
        if not gated:
            return await call_next(request)
        auth = request.headers.get("Authorization") or ""
        if not auth.lower().startswith("bearer "):
            return JSONResponse({"detail": "missing_bearer_token"}, status_code=401)
        token = auth[7:].strip()
        if token != expected:
            return JSONResponse({"detail": "invalid_api_key"}, status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def omega_inflight_tracker(request: Request, call_next):
        """Track in-flight requests + reject new ``/v1/*`` traffic
        during shutdown drain.

        Declared BEFORE ``omega_request_id`` so request_id stays
        OUTERMOST (LIFO) — drain-rejected requests still get a
        traceable X-Request-ID for operator forensics.

        Health endpoint stays open during drain so operators / load
        balancers can see the shutting-down state.
        """
        tracker = getattr(request.app.state, "inflight_tracker", None)
        if tracker is None:
            return await call_next(request)
        path = request.url.path or ""
        if tracker.shutting_down and (path.startswith("/v1") or path.startswith("/admin")):
            return JSONResponse(
                {"detail": "service_draining"},
                status_code=503,
                headers={"Retry-After": "5"},
            )
        await tracker.acquire()
        try:
            return await call_next(request)
        finally:
            await tracker.release()

    @app.middleware("http")
    async def omega_request_id(request: Request, call_next):
        """Allocate / inherit a request_id, bind it to the async context
        so every downstream log record + handler can read it, and return
        it to the client as X-Request-ID.

        Declared LAST so it runs FIRST (Starlette LIFO middleware order)
        — even drain-rejected and auth-rejected requests get a
        traceable ID, which is what operators want to grep for most.
        Logging filter installed at app creation time pulls the
        contextvar value onto every record.

        If the client sent X-Request-ID (e.g. distributed tracing from
        an upstream proxy), respect it after sanitizing. Otherwise
        allocate a fresh UUID4."""
        upstream = _sanitize_request_id(request.headers.get("x-request-id", ""))
        request_id = upstream or new_request_id()
        ctx_token = set_current_request_id(request_id)
        try:
            response = await call_next(request)
        finally:
            reset_current_request_id(ctx_token)
        response.headers["X-Request-ID"] = request_id
        return response

    app.include_router(routes_health.router)
    app.include_router(routes_v1.router)
    app.include_router(routes_hub.router)
    app.include_router(routes_admin.router)
    return app


app = create_app()
