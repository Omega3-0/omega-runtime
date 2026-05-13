from __future__ import annotations

import importlib


def test_fastapi_version_uses_package_version(monkeypatch) -> None:
    app_mod = importlib.import_module("omega_studio.server.app")
    monkeypatch.setattr(app_mod, "__version__", "9.9.9-test")
    app = app_mod.create_app()

    assert app.version == "9.9.9-test"
