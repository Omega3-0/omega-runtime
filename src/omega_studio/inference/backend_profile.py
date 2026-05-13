"""Probe accelerators (ONNX Runtime EPs, llama.cpp GPU), vendor bin layout, persist profile."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from omega_studio.inference.backends import (
    default_llama_n_gpu_layers,
    ensure_vendor_bin_on_path,
    gpu_acceleration_detected,
    order_ort_providers,
    resolved_ort_ep_preference,
    vendor_accelerators_bin,
)
from omega_studio.paths import app_data_dir, bundle_root

log = logging.getLogger("omega_studio.inference.backend_profile")


def probe_onnx_ep_order() -> list[str]:
    """Return ORT ``get_available_providers()`` order when onnxruntime is importable."""
    try:
        import onnxruntime as ort  # type: ignore

        return list(ort.get_available_providers())
    except Exception as exc:
        log.debug("onnxruntime probe skipped: %s", exc)
        return []


def probe_llama_gpu_capable() -> bool:
    """True when llama.cpp reports GPU offload support (CUDA/Metal/Vulkan backends)."""
    try:
        import llama_cpp  # type: ignore

        fn = getattr(llama_cpp, "llama_supports_gpu_offload", None)
        if callable(fn):
            return bool(fn())
        fn2 = getattr(getattr(llama_cpp, "llama_cpp", None), "llama_supports_gpu_offload", None)
        if callable(fn2):
            return bool(fn2())
    except Exception as exc:
        log.debug("llama_cpp GPU probe skipped: %s", exc)
    return False


def probe_vendor_bin_files(*, limit: int = 40) -> list[str]:
    vb = vendor_accelerators_bin()
    if not vb:
        return []
    names: list[str] = []
    try:
        for ch in sorted(vb.iterdir()):
            if ch.is_file() and len(names) < limit:
                names.append(ch.name)
    except OSError:
        pass
    return names


def backend_profile_path() -> Path:
    return app_data_dir() / "backend_profile.json"


def build_backend_profile_dict() -> dict[str, Any]:
    ensure_vendor_bin_on_path()
    vb = vendor_accelerators_bin()
    raw_eps = probe_onnx_ep_order()
    pref = resolved_ort_ep_preference()
    if raw_eps and pref:
        ort_ordered = order_ort_providers(raw_eps, preferred=pref)
    elif raw_eps:
        ort_ordered = order_ort_providers(raw_eps)
    else:
        ort_ordered = []
    return {
        "version": 1,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "onnx_ep_order": raw_eps,
        "ort_providers_ordered": ort_ordered,
        "gpu_acceleration_detected": gpu_acceleration_detected(
            ort_providers=raw_eps if raw_eps else None
        ),
        "default_llama_n_gpu_layers": default_llama_n_gpu_layers(),
        "llama_gpu_capable": probe_llama_gpu_capable(),
        "vendor_accelerators_bin": str(vb) if vb else None,
        "vendor_bin_sample": probe_vendor_bin_files(),
        "bundle_root": str(bundle_root()),
    }


def save_backend_profile(data: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = data or build_backend_profile_dict()
    path = backend_profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)
    log.info("wrote backend profile -> %s", path)
    return payload


def load_backend_profile() -> dict[str, Any]:
    path = backend_profile_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("backend profile read failed: %s", exc)
        return {}


def refresh_backend_profile() -> dict[str, Any]:
    """Run probes and persist; safe to call at startup."""
    try:
        return save_backend_profile()
    except Exception as exc:
        log.warning("backend profile refresh failed: %s", exc)
        return {}


def apply_backend_profile_hints(reg: Any) -> None:
    """Mutate registry settings when env does not force GPU layer count.

    If llama.cpp has no GPU offload and ``n_gpu_layers`` is still the default
    ``-1``, force ``0`` to avoid runtime errors on CPU-only wheels.
    """
    if os.environ.get("OMEGA_GLOBAL_N_GPU_LAYERS"):
        return
    prof = load_backend_profile()
    if not prof:
        return
    if getattr(reg.settings, "n_gpu_layers", None) != -1:
        return
    if prof.get("llama_gpu_capable") is False or prof.get("gpu_acceleration_detected") is False:
        reg.settings.n_gpu_layers = 0
        log.info("backend_profile: CPU-only hints -> n_gpu_layers=0")
