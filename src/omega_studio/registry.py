"""Model folder scanning and persisted JSON registry."""

from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path

from omega_studio.config import ModelRecord, RegistryFile
from omega_studio.paths import app_data_dir, ensure_app_dirs

log = logging.getLogger("omega_studio.registry")


def apply_env_overrides(reg: RegistryFile) -> RegistryFile:
    """Overlay process env onto settings (does not persist). LM Studio–style flags."""
    import os

    reg = reg.model_copy(deep=True)
    st = reg.settings
    if os.environ.get("OMEGA_GLOBAL_N_CTX"):
        st.n_ctx = int(os.environ["OMEGA_GLOBAL_N_CTX"])
    if os.environ.get("OMEGA_GLOBAL_N_GPU_LAYERS"):
        st.n_gpu_layers = int(os.environ["OMEGA_GLOBAL_N_GPU_LAYERS"])
    if os.environ.get("OMEGA_GLOBAL_TEMPERATURE"):
        st.temperature = float(os.environ["OMEGA_GLOBAL_TEMPERATURE"])
    if os.environ.get("OMEGA_GLOBAL_TOP_P"):
        st.top_p = float(os.environ["OMEGA_GLOBAL_TOP_P"])
    if os.environ.get("OMEGA_STUDIO_HOST"):
        st.server_host = os.environ["OMEGA_STUDIO_HOST"]
    if os.environ.get("OMEGA_STUDIO_PORT"):
        st.server_port = int(os.environ["OMEGA_STUDIO_PORT"])
    if os.environ.get("OMEGA_MAX_CONCURRENT_MODELS"):
        st.max_concurrent_models = min(15, max(1, int(os.environ["OMEGA_MAX_CONCURRENT_MODELS"])))
    try:
        from omega_studio.inference.backend_profile import apply_backend_profile_hints

        apply_backend_profile_hints(reg)
    except Exception:
        pass
    return reg


MODEL_SUFFIXES = {".gguf", ".ggml", ".onnx", ".bin", ".safetensors", ".pt", ".pth", ".engine"}

SAFE_ID = re.compile(r"[^a-zA-Z0-9._-]+")


def registry_path() -> Path:
    ensure_app_dirs()
    return app_data_dir() / "registry.json"


def default_registry() -> RegistryFile:
    _, models = ensure_app_dirs()
    return RegistryFile(
        model_folders=[str(models)],
        models={},
    )


def load_registry() -> RegistryFile:
    path = registry_path()
    if not path.is_file():
        reg = default_registry()
        save_registry(reg)
        return reg
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return RegistryFile.model_validate(data)
    except Exception as exc:
        log.warning("registry load failed (%s), resetting", exc)
        reg = default_registry()
        save_registry(reg)
        return reg


def save_registry(reg: RegistryFile) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(reg.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)


def _sanitize_model_id(name: str) -> str:
    base = SAFE_ID.sub("-", Path(name).stem).strip("-_.") or "model"
    return base[:80]


def scan_folders(folders: list[str]) -> dict[str, ModelRecord]:
    found: dict[str, ModelRecord] = {}
    seen_paths: set[str] = set()
    for raw in folders:
        root = Path(raw).expanduser()
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in MODEL_SUFFIXES:
                continue
            key = str(p.resolve())
            if key in seen_paths:
                continue
            seen_paths.add(key)
            mid = _sanitize_model_id(p.name)
            if mid in found:
                mid = f"{mid}-{uuid.uuid4().hex[:6]}"
            fmt = p.suffix.lower().lstrip(".")
            found[mid] = ModelRecord(path=key, format=fmt, display_name=p.name)
    return found


def merge_scan_into_registry(reg: RegistryFile) -> tuple[RegistryFile, int]:
    """Add newly discovered files; do not remove operator-curated entries."""
    discovered = scan_folders(reg.model_folders)
    added = 0
    for mid, rec in discovered.items():
        if mid not in reg.models:
            reg.models[mid] = rec
            added += 1
    return reg, added
