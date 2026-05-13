from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

from omega_studio.downloads.hf_download import download_hf_file


def test_download_hf_file_passes_env_token(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    def fake_download(**kwargs: Any) -> str:
        calls.append(kwargs)
        out = tmp_path / "model.gguf"
        out.write_text("ok", encoding="utf-8")
        return str(out)

    fake_mod = types.ModuleType("huggingface_hub")
    fake_mod.hf_hub_download = fake_download
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_mod)
    monkeypatch.setenv("HF_TOKEN", "hf-secret")

    got = download_hf_file("org/repo", "model.gguf", tmp_path)

    assert got == tmp_path / "model.gguf"
    assert calls[0]["token"] == "hf-secret"
    assert "resume_download" not in calls[0]
    assert "local_dir_use_symlinks" not in calls[0]


def test_download_hf_file_streams_progress_with_token(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []
    progress: list[float] = []

    fake_mod = types.ModuleType("huggingface_hub")
    fake_mod.hf_hub_url = lambda **_kwargs: "https://huggingface.local/model.gguf"
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_mod)
    monkeypatch.setenv("HF_TOKEN", "hf-secret")

    def fake_download_url_resume(url: str, dest: Path, **kwargs: Any) -> None:
        calls.append({"url": url, "dest": dest, **kwargs})
        kwargs["progress"](0.5)
        kwargs["progress"](1.0)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("ok", encoding="utf-8")

    monkeypatch.setattr(
        "omega_studio.downloads.hf_download.download_url_resume",
        fake_download_url_resume,
    )

    got = download_hf_file("org/repo", "nested/model.gguf", tmp_path, progress=progress.append)

    assert got == tmp_path / "nested" / "model.gguf"
    assert progress == [0.5, 1.0]
    assert calls[0]["headers"] == {"Authorization": "Bearer hf-secret"}
