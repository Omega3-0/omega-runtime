"""Minimal admin surface (protect with ``OMEGA_API_KEY`` when enabled)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from omega_studio.registry import apply_env_overrides, load_registry

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/status")
def admin_status(request: Request):
    snap = getattr(request.app.state, "backend_snapshot", None)
    body: dict = {"ok": True, "service": "omega-runtime-studio"}
    if snap is not None:
        body["backend_profile"] = snap.to_public_dict()
    else:
        body["backend_profile"] = None
    return body


def _registry(request: Request):
    reg = getattr(request.app.state, "registry", None)
    if reg is None:
        reg = load_registry()
        request.app.state.registry = reg
    return apply_env_overrides(reg)


@router.post("/models/{model_id}/load")
def admin_load_model(request: Request, model_id: str):
    reg = _registry(request)
    if model_id not in reg.models:
        raise HTTPException(404, f"unknown model: {model_id}")
    rec = reg.models[model_id]
    fmt = (rec.format or "").lower()
    if fmt not in ("gguf", "ggml"):
        raise HTTPException(400, f"format not supported for load: {fmt}")
    from omega_studio.server.routes_v1 import _prepare_gguf_model, _resolve_generation_params

    params = _resolve_generation_params(reg, model_id)
    try:
        meta = _prepare_gguf_model(request, model_id, rec, params)
    except Exception as exc:
        raise HTTPException(500, f"load failed: {exc}") from exc
    return {"ok": True, "id": model_id, **meta}


@router.post("/models/{model_id}/unload")
def admin_unload_model(request: Request, model_id: str):
    reg = _registry(request)
    if model_id not in reg.models:
        raise HTTPException(404, f"unknown model: {model_id}")
    request.app.state.engine.unload(model_id)
    rm = getattr(request.app.state, "resource_manager", None)
    entries = getattr(rm, "_entries", None)
    if isinstance(entries, dict):
        entries.pop(model_id, None)
    return {"ok": True, "id": model_id, "loaded": False}
