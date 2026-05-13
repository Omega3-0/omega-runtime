"""Operator GUI preferences (env hints, ORT order) — not the model registry."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from omega_studio.paths import app_data_dir


def gui_settings_path() -> Path:
    return app_data_dir() / "gui_settings.json"


class EnvVarRow(BaseModel):
    key: str = ""
    value: str = ""


class WindowState(BaseModel):
    """Last main-window geometry (normalGeometry), restored on next launch."""

    x: int | None = None
    y: int | None = None
    width: int | None = None
    height: int | None = None
    maximized: bool = False


class PortableGuiSettings(BaseModel):
    """Persisted next to registry under %LOCALAPPDATA%\\Omega3Portable\\gui_settings.json."""

    version: int = 1
    ort_ep_order: list[str] = Field(
        default_factory=lambda: [
            "CUDAExecutionProvider",
            "DmlExecutionProvider",
            "CPUExecutionProvider",
        ],
        description="Preferred ONNX Runtime EP order (full names or short aliases).",
    )
    prefer_vulkan_llama: bool = False
    omega_bundle_root: str = ""
    omega_runtime_harvest: str = ""
    omega_api_key: str = ""
    omega_gateway_gguf_workers: str = ""
    custom_env: list[EnvVarRow] = Field(default_factory=list)
    # Session UI (survives restarts; model weights paths live in registry.json).
    window: WindowState = Field(default_factory=WindowState)
    main_tab_index: int = 0
    last_selected_model_id: str = ""
    playground_model_id: str = ""
    playground_max_tokens: int | None = None
    playground_show_thinking: bool | None = None
    sync_runtime_on_apply: bool = False
    downloads_hf_repo: str = ""
    downloads_hf_file: str = ""
    downloads_dest: str = ""
    downloads_url: str = ""


def load_gui_settings() -> PortableGuiSettings:
    path = gui_settings_path()
    if not path.is_file():
        return PortableGuiSettings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return PortableGuiSettings.model_validate(data)
    except Exception:
        return PortableGuiSettings()


def save_gui_settings(settings: PortableGuiSettings) -> None:
    path = gui_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)


def gui_settings_to_child_env(base: dict[str, str], gs: PortableGuiSettings) -> dict[str, str]:
    """Merge stored paths and custom rows into a copy of *base* for subprocess/server."""
    env = dict(base)
    if gs.omega_bundle_root.strip():
        env["OMEGA_BUNDLE_ROOT"] = gs.omega_bundle_root.strip()
    if gs.omega_runtime_harvest.strip():
        env["OMEGA_RUNTIME_HARVEST"] = gs.omega_runtime_harvest.strip()
    if gs.omega_api_key.strip():
        env["OMEGA_API_KEY"] = gs.omega_api_key.strip()
    if gs.omega_gateway_gguf_workers.strip():
        env["OMEGA_GATEWAY_GGUF_WORKERS"] = gs.omega_gateway_gguf_workers.strip()
    if gs.ort_ep_order:
        env["OMEGA_ORT_EP_ORDER"] = ",".join(gs.ort_ep_order)
    if gs.prefer_vulkan_llama:
        env["OMEGA_PREFER_VULKAN_LLAMA"] = "1"
    else:
        env.pop("OMEGA_PREFER_VULKAN_LLAMA", None)
    for row in gs.custom_env:
        k = (row.key or "").strip()
        if not k:
            continue
        env[k] = row.value
    return env


def ort_aliases_to_execution_providers(tokens: list[str]) -> list[str]:
    """Map GUI tokens (short or full EP names) to ORT provider strings."""
    alias: dict[str, str] = {
        "cuda": "CUDAExecutionProvider",
        "dml": "DmlExecutionProvider",
        "cpu": "CPUExecutionProvider",
        "vitis": "VitisAIExecutionProvider",
        "tensorrt": "TensorrtExecutionProvider",
        "openvino": "OpenVINOExecutionProvider",
    }
    out: list[str] = []
    seen: set[str] = set()
    for raw in tokens:
        s = (raw or "").strip()
        if not s:
            continue
        low = s.lower()
        mapped = alias.get(low, s)
        if mapped not in seen:
            seen.add(mapped)
            out.append(mapped)
    return out
