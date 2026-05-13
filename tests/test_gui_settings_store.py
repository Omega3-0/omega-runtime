"""gui_settings_store persistence (window + session fields)."""

from __future__ import annotations

from pathlib import Path

from omega_studio.gui_settings_store import (
    WindowState,
    load_gui_settings,
    save_gui_settings,
)


def test_gui_settings_roundtrip_window_and_session(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "omega_studio.gui_settings_store.gui_settings_path",
        lambda: tmp_path / "gui_settings.json",
    )
    gs = load_gui_settings()
    gs.window = WindowState(x=10, y=20, width=800, height=600, maximized=True)
    gs.main_tab_index = 3
    gs.last_selected_model_id = "my-gguf"
    gs.playground_model_id = "my-gguf"
    gs.playground_max_tokens = 512
    gs.playground_show_thinking = False
    gs.sync_runtime_on_apply = True
    gs.downloads_hf_repo = "org/repo"
    save_gui_settings(gs)
    out = tmp_path / "gui_settings.json"
    assert out.is_file()
    gs2 = load_gui_settings()
    assert gs2.window.x == 10
    assert gs2.window.maximized is True
    assert gs2.main_tab_index == 3
    assert gs2.last_selected_model_id == "my-gguf"
    assert gs2.playground_max_tokens == 512
    assert gs2.playground_show_thinking is False
    assert gs2.sync_runtime_on_apply is True
    assert gs2.downloads_hf_repo == "org/repo"
