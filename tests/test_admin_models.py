from __future__ import annotations

import importlib

from starlette.testclient import TestClient

from omega_studio.config import ModelRecord, RegistryFile, StudioSettings


def _registry() -> RegistryFile:
    return RegistryFile(
        version=1,
        model_folders=[],
        models={"stub-model": ModelRecord(path=r"C:\fake\stub.gguf", format="gguf")},
        settings=StudioSettings(),
    )


class _AdminEngine:
    def __init__(self) -> None:
        self.events: list[str] = []

    def loaded_ids(self) -> list[str]:
        return []

    def is_loaded(self, model_id: str) -> bool:
        return False

    def load_gguf(self, model_id: str, *_args, **_kwargs) -> None:
        self.events.append(f"load:{model_id}")

    def unload(self, model_id: str) -> None:
        self.events.append(f"unload:{model_id}")


def test_admin_model_load_and_unload(monkeypatch) -> None:
    app_mod = importlib.import_module("omega_studio.server.app")
    import omega_studio.server.routes_admin as ra

    reg = _registry()
    monkeypatch.setattr(app_mod, "load_registry", lambda: reg)
    monkeypatch.setattr(ra, "apply_env_overrides", lambda r: r)
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    engine = _AdminEngine()
    with TestClient(app) as client:
        client.app.state.engine = engine
        r_load = client.post("/admin/models/stub-model/load")
        r_unload = client.post("/admin/models/stub-model/unload")

    assert r_load.status_code == 200
    assert r_load.json()["loaded"] is True
    assert r_load.json()["load_duration_s"] >= 0
    assert r_load.json()["n_ctx"] == 8192
    assert r_load.json()["n_gpu_layers"] in (-1, 0)
    assert r_load.json()["embedding"] is False
    assert r_load.json()["vram_estimate_mb"] is None
    assert r_unload.status_code == 200
    assert r_unload.json()["loaded"] is False
    assert engine.events == ["load:stub-model", "unload:stub-model"]
