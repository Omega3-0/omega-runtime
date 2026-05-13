"""ONNX Runtime provider ordering, GPU heuristics, vendor bin path, persisted backend profile."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from omega_studio.gui_settings_store import load_gui_settings, ort_aliases_to_execution_providers
from omega_studio.paths import app_data_dir, bundle_root

log = logging.getLogger("omega_studio.inference.backends")

PREFERRED_ORT_ORDER: tuple[str, ...] = (
    "CUDAExecutionProvider",
    "DmlExecutionProvider",
    "CPUExecutionProvider",
)


def vendor_accelerators_bin() -> Path | None:
    """``vendor/accelerators/bin`` under bundle (``OMEGA_BUNDLE_ROOT`` or frozen dir).

    Falls back to legacy ``vendor/lemonade/bin`` when present so older portable trees
    keep working without a rename.
    """
    root = bundle_root()
    p_new = root / "vendor" / "accelerators" / "bin"
    if p_new.is_dir():
        return p_new
    p_old = root / "vendor" / "lemonade" / "bin"
    return p_old if p_old.is_dir() else None


def ensure_vendor_bin_on_path() -> Path | None:
    """Prepend vendored ORT / llama DLL directory (Windows ``add_dll_directory``)."""
    vb = vendor_accelerators_bin()
    if not vb:
        return None
    key = str(vb.resolve())
    path = os.environ.get("PATH", "")
    if key not in path.split(os.pathsep):
        os.environ["PATH"] = key + os.pathsep + path
    if os.name == "nt":
        try:
            os.add_dll_directory(key)  # type: ignore[attr-defined]
        except (OSError, AttributeError) as exc:
            log.debug("add_dll_directory skipped: %s", exc)
    return vb


def order_ort_providers(
    available: Sequence[str],
    preferred: Sequence[str] | None = None,
) -> list[str]:
    """Order ORT EPs: *preferred* (or default) ∩ *available*, then the rest."""
    avail = set(str(x) for x in available)
    pref: tuple[str, ...] = tuple(preferred) if preferred is not None else PREFERRED_ORT_ORDER
    ordered = [p for p in pref if p in avail]
    if ordered:
        rest = sorted(avail - set(ordered))
        return ordered + rest
    return sorted(avail)


def resolved_ort_ep_preference() -> list[str] | None:
    """Prefer ``OMEGA_ORT_EP_ORDER`` env; else persisted GUI EP list (aliases allowed)."""

    raw = os.environ.get("OMEGA_ORT_EP_ORDER", "").strip()
    if raw:
        tokens = [x.strip() for x in raw.split(",") if x.strip()]
        mapped = ort_aliases_to_execution_providers(tokens)
        return mapped or None
    try:
        gs = load_gui_settings()
        if not gs.ort_ep_order:
            return None
        tokens = [str(x) for x in gs.ort_ep_order]
        mapped = ort_aliases_to_execution_providers(tokens)
        return mapped or None
    except Exception:
        return None


def _read_cuda_path() -> Path | None:
    raw = os.environ.get("CUDA_PATH") or os.environ.get("CUDA_HOME")
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p if p.is_dir() else None


def _torch_cuda_available() -> bool | None:
    try:
        import torch  # type: ignore

        return bool(torch.cuda.is_available())
    except Exception:
        return None


def _onnxruntime_providers() -> list[str] | None:
    try:
        import onnxruntime as ort  # type: ignore

        return list(ort.get_available_providers())
    except Exception:
        return None


def _llama_supports_gpu_offload_api() -> bool | None:
    try:
        import llama_cpp  # type: ignore

        fn = getattr(llama_cpp, "llama_supports_gpu_offload", None)
        if callable(fn):
            return bool(fn())
        fn2 = getattr(getattr(llama_cpp, "llama_cpp", None), "llama_supports_gpu_offload", None)
        if callable(fn2):
            return bool(fn2())
    except Exception:
        pass
    return None


def _llama_cpp_gpu_build_hint() -> bool:
    """Detect CUDA/Vulkan-capable llama-cpp-python wheel without loading a model."""
    if _llama_supports_gpu_offload_api() is True:
        return True
    try:
        from llama_cpp import llama_cpp  # type: ignore
    except Exception:
        return False
    mod_file = getattr(llama_cpp, "__file__", "") or ""
    low = str(mod_file).lower()
    if "cuda" in low or "vulkan" in low or "gpu" in low:
        return True
    pkg_root = Path(mod_file).parent
    for pat in ("*cuda*", "*vulkan*", "*ggml-cuda*", "*ggml-vulkan*"):
        try:
            if any(pkg_root.glob(pat)):
                return True
        except Exception:
            continue
    return False


def _vulkan_sdk_present() -> bool:
    return bool(os.environ.get("VULKAN_SDK"))


def gpu_acceleration_detected(
    *,
    ort_providers: list[str] | None = None,
    torch_cuda: bool | None = None,
    cuda_path: Path | None = None,
    llama_gpu_wheel: bool | None = None,
    vulkan_sdk: bool | None = None,
) -> bool:
    """True if any signal suggests GPU offload is plausible for llama.cpp / ORT."""
    providers = ort_providers if ort_providers is not None else _onnxruntime_providers()
    if providers:
        if "CUDAExecutionProvider" in providers or "DmlExecutionProvider" in providers:
            return True
    tc = torch_cuda if torch_cuda is not None else _torch_cuda_available()
    if tc is True:
        return True
    cp = cuda_path if cuda_path is not None else _read_cuda_path()
    if cp is not None:
        return True
    lg = llama_gpu_wheel if llama_gpu_wheel is not None else _llama_cpp_gpu_build_hint()
    if lg:
        return True
    vs = vulkan_sdk if vulkan_sdk is not None else _vulkan_sdk_present()
    if vs:
        return True
    return False


_VALID_VARIANTS: tuple[str, ...] = ("cpu", "cuda", "vulkan", "dml")


def _baked_in_variant() -> str:
    """Read the build-time variant constant baked into the bundle.

    `omega_studio._build_info` is generated by `scripts/build_windows.ps1`
    immediately before PyInstaller runs. It's NOT checked into git — dev
    runs gracefully fall through (the import fails) and the env-var path
    takes over. Frozen bundles always have it.
    """
    try:
        from omega_studio._build_info import BUILD_VARIANT
    except ImportError:
        return ""
    return str(BUILD_VARIANT or "").strip().lower()


def get_omega_variant() -> str:
    """Resolve the build variant (cpu / cuda / vulkan / dml).

    Resolution order:
      1. ``OMEGA_VARIANT`` env var — operator runtime override
      2. Baked-in `_build_info.BUILD_VARIANT` — set by build scripts
      3. Default ``"cpu"``

    Surfaced on ``/v1/version`` and consumed by ``_resolve_backend()``.
    """
    env = (os.environ.get("OMEGA_VARIANT") or "").strip().lower()
    raw = env if env in _VALID_VARIANTS else _baked_in_variant()
    return raw if raw in _VALID_VARIANTS else "cpu"


def _resolve_backend(preferred: str | None, model_format: str) -> str:
    """Determine actual backend based on model format and system capability."""
    variant = get_omega_variant()
    if preferred == "auto":
        if model_format == "onnx":
            return "onnxruntime"
        if variant == "cuda":
            return "cuda"
        if variant == "vulkan":
            return "vulkan"
        return "cpu"
    return preferred or "cpu"


def default_llama_n_gpu_layers() -> int:
    """-1 = all layers on GPU when environment suggests GPU; else 0."""
    if gpu_acceleration_detected():
        return -1
    return 0


def effective_llama_n_gpu_layers(configured: int) -> int:
    """Registry/env uses -1 as 'auto': map to hardware-aware default."""
    if configured != -1:
        return configured
    return default_llama_n_gpu_layers()


@dataclass
class BackendSnapshot:
    ort_providers_available: list[str] = field(default_factory=list)
    ort_providers_ordered: list[str] = field(default_factory=list)
    torch_cuda_available: bool | None = None
    cuda_path_set: bool = False
    llama_cpp_gpu_wheel_hint: bool = False
    llama_supports_gpu_offload: bool | None = None
    vulkan_sdk_present: bool = False
    gpu_acceleration_detected: bool = False
    default_llama_n_gpu_layers: int = 0
    profile_path: str = ""
    vendor_accelerators_bin: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "ort_providers_available": list(self.ort_providers_available),
            "ort_providers_ordered": list(self.ort_providers_ordered),
            "torch_cuda_available": self.torch_cuda_available,
            "cuda_path_set": self.cuda_path_set,
            "llama_cpp_gpu_wheel_hint": self.llama_cpp_gpu_wheel_hint,
            "llama_supports_gpu_offload": self.llama_supports_gpu_offload,
            "vulkan_sdk_present": self.vulkan_sdk_present,
            "gpu_acceleration_detected": self.gpu_acceleration_detected,
            "default_llama_n_gpu_layers": int(self.default_llama_n_gpu_layers),
            "profile_path": self.profile_path,
            "vendor_accelerators_bin": self.vendor_accelerators_bin,
        }


def build_backend_snapshot(
    *,
    get_ort_providers: Callable[[], list[str] | None] | None = None,
) -> BackendSnapshot:
    ensure_vendor_bin_on_path()
    get_ort = get_ort_providers or _onnxruntime_providers
    raw = get_ort()
    avail = list(raw or [])
    pref = resolved_ort_ep_preference()
    if avail and pref:
        ordered = order_ort_providers(avail, preferred=pref)
    elif avail:
        ordered = order_ort_providers(avail)
    else:
        ordered = []
    torch_cuda = _torch_cuda_available()
    cuda_path = _read_cuda_path()
    offload_api = _llama_supports_gpu_offload_api()
    llama_hint = _llama_cpp_gpu_build_hint()
    vulkan = _vulkan_sdk_present()
    vb = vendor_accelerators_bin()
    gpu = gpu_acceleration_detected(
        ort_providers=avail,
        torch_cuda=torch_cuda,
        cuda_path=cuda_path,
        llama_gpu_wheel=llama_hint,
        vulkan_sdk=vulkan,
    )
    snap = BackendSnapshot(
        ort_providers_available=avail,
        ort_providers_ordered=ordered,
        torch_cuda_available=torch_cuda,
        cuda_path_set=cuda_path is not None,
        llama_cpp_gpu_wheel_hint=llama_hint,
        llama_supports_gpu_offload=offload_api,
        vulkan_sdk_present=vulkan,
        gpu_acceleration_detected=gpu,
        default_llama_n_gpu_layers=default_llama_n_gpu_layers(),
        profile_path=str(app_data_dir() / "backend_profile.json"),
        vendor_accelerators_bin=str(vb) if vb else None,
    )
    return snap
