from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from omega_studio.inference.backend_profile import save_backend_profile
from omega_studio.inference.backends import (
    BackendSnapshot,
    build_backend_snapshot,
    default_llama_n_gpu_layers,
    effective_llama_n_gpu_layers,
    gpu_acceleration_detected,
    order_ort_providers,
)


def test_order_ort_providers_cuda_first():
    avail = ["CPUExecutionProvider", "CUDAExecutionProvider", "DmlExecutionProvider"]
    assert order_ort_providers(avail) == [
        "CUDAExecutionProvider",
        "DmlExecutionProvider",
        "CPUExecutionProvider",
    ]


def test_order_ort_providers_cpu_only():
    assert order_ort_providers(["CPUExecutionProvider"]) == ["CPUExecutionProvider"]


def test_order_ort_providers_respects_custom_preference():
    avail = ["CPUExecutionProvider", "CUDAExecutionProvider", "DmlExecutionProvider"]
    pref = ["DmlExecutionProvider", "CPUExecutionProvider", "CUDAExecutionProvider"]
    assert order_ort_providers(avail, preferred=pref) == [
        "DmlExecutionProvider",
        "CPUExecutionProvider",
        "CUDAExecutionProvider",
    ]


def test_order_ort_providers_exotic_fallback():
    assert order_ort_providers(["AzureExecutionProvider"]) == ["AzureExecutionProvider"]


def test_gpu_acceleration_from_ort_cuda():
    providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    assert gpu_acceleration_detected(ort_providers=providers)


def test_gpu_acceleration_from_torch():
    assert gpu_acceleration_detected(ort_providers=[], torch_cuda=True) is True


def test_gpu_acceleration_negative(monkeypatch):
    monkeypatch.setattr("omega_studio.inference.backends._read_cuda_path", lambda: None)
    monkeypatch.setattr(
        "omega_studio.inference.backends._llama_supports_gpu_offload_api",
        lambda: None,
    )
    monkeypatch.setattr("omega_studio.inference.backends._llama_cpp_gpu_build_hint", lambda: False)
    monkeypatch.setattr("omega_studio.inference.backends._vulkan_sdk_present", lambda: False)
    got = gpu_acceleration_detected(ort_providers=["CPUExecutionProvider"], torch_cuda=False)
    assert got is False


def test_effective_llama_layers_respects_explicit():
    assert effective_llama_n_gpu_layers(8) == 8


def test_effective_llama_auto_zero_without_gpu():
    assert effective_llama_n_gpu_layers(-1) == default_llama_n_gpu_layers()


def test_build_backend_snapshot_with_mock_ort():
    def fake_providers():
        return ["CPUExecutionProvider", "CUDAExecutionProvider"]

    snap = build_backend_snapshot(get_ort_providers=fake_providers)
    assert snap.ort_providers_ordered == ["CUDAExecutionProvider", "CPUExecutionProvider"]


def test_save_backend_profile_includes_ordered_eps(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "omega_studio.inference.backend_profile.backend_profile_path",
        lambda: tmp_path / "backend_profile.json",
    )
    monkeypatch.setattr(
        "omega_studio.inference.backend_profile.probe_onnx_ep_order",
        lambda: ["CPUExecutionProvider", "CUDAExecutionProvider"],
    )
    import importlib

    bp = importlib.import_module("omega_studio.inference.backend_profile")
    payload = bp.build_backend_profile_dict()
    assert payload["ort_providers_ordered"] == ["CUDAExecutionProvider", "CPUExecutionProvider"]
    save_backend_profile(payload)
    assert (tmp_path / "backend_profile.json").is_file()


def test_vendor_accelerators_prefers_new_layout(monkeypatch, tmp_path: Path):
    from omega_studio.inference import backends as b

    root = tmp_path / "bundle"
    (root / "vendor" / "accelerators" / "bin").mkdir(parents=True)
    (root / "vendor" / "lemonade" / "bin").mkdir(parents=True)
    monkeypatch.setattr(b, "bundle_root", lambda: root)
    assert b.vendor_accelerators_bin() == root / "vendor" / "accelerators" / "bin"


def test_vendor_accelerators_legacy_fallback(monkeypatch, tmp_path: Path):
    from omega_studio.inference import backends as b

    root = tmp_path / "bundle"
    (root / "vendor" / "lemonade" / "bin").mkdir(parents=True)
    monkeypatch.setattr(b, "bundle_root", lambda: root)
    assert b.vendor_accelerators_bin() == root / "vendor" / "lemonade" / "bin"


def test_health_and_models_include_backend(monkeypatch, tmp_path: Path):
    fixed = BackendSnapshot(
        ort_providers_available=["CPUExecutionProvider"],
        ort_providers_ordered=["CPUExecutionProvider"],
        torch_cuda_available=False,
        cuda_path_set=False,
        llama_cpp_gpu_wheel_hint=False,
        llama_supports_gpu_offload=False,
        vulkan_sdk_present=False,
        gpu_acceleration_detected=False,
        default_llama_n_gpu_layers=0,
        profile_path=str(tmp_path / "backend_profile.json"),
        vendor_accelerators_bin=None,
    )

    import importlib

    app_mod = importlib.import_module("omega_studio.server.app")
    monkeypatch.setattr(app_mod, "build_backend_snapshot", lambda: fixed)
    app = app_mod.create_app()
    with TestClient(app) as client:
        h = client.get("/health").json()
        assert h["status"] == "ok"
        assert h["backend"]["ort_providers_ordered"] == ["CPUExecutionProvider"]
        m = client.get("/v1/models").json()
        assert m["omega_backend"]["ort_providers_ordered"] == ["CPUExecutionProvider"]
