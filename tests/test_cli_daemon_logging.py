from __future__ import annotations

from pathlib import Path

from omega_studio.cli import _rotate_log_if_needed


def test_rotate_log_if_needed_moves_oversized_log(tmp_path: Path) -> None:
    log_path = tmp_path / "daemon.log"
    log_path.write_text("x" * 20, encoding="utf-8")

    rotated = _rotate_log_if_needed(log_path, max_bytes=10)

    assert rotated == log_path.with_suffix(".log.1")
    assert rotated.is_file()
    assert not log_path.exists()
