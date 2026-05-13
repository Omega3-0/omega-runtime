from __future__ import annotations

from omega_studio.config import RegistryFile, StudioSettings
from omega_studio.registry import apply_env_overrides


def test_apply_env_overrides_does_not_mutate_persisted_registry(monkeypatch) -> None:
    reg = RegistryFile(settings=StudioSettings(server_port=11434, n_gpu_layers=-1))
    monkeypatch.setenv("OMEGA_STUDIO_PORT", "11500")

    out = apply_env_overrides(reg)

    assert out.settings.server_port == 11500
    assert reg.settings.server_port == 11434


def test_backend_profile_hints_do_not_mutate_persisted_registry(monkeypatch) -> None:
    import omega_studio.inference.backend_profile as bp

    reg = RegistryFile(settings=StudioSettings(n_gpu_layers=-1))
    monkeypatch.setattr(
        bp,
        "apply_backend_profile_hints",
        lambda r: setattr(r.settings, "n_gpu_layers", 0),
    )

    out = apply_env_overrides(reg)

    assert out.settings.n_gpu_layers == 0
    assert reg.settings.n_gpu_layers == -1
