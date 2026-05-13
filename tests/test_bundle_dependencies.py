from __future__ import annotations

import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_runtime_requirements_bundle_llama_cpp_python() -> None:
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert "\nllama-cpp-python" in f"\n{req}"
    assert "# llama-cpp-python" not in req


def test_project_dependencies_include_llama_cpp_python() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]

    assert any(str(dep).startswith("llama-cpp-python") for dep in deps)
