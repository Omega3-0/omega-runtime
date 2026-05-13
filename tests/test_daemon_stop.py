"""CLI daemon-stop helper."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from omega_studio import cli as cli_mod


def test_daemon_stop_requires_arg():
    r = CliRunner().invoke(cli_mod.app, ["daemon-stop"])
    assert r.exit_code != 0


def test_daemon_stop_missing_file():
    r = CliRunner().invoke(
        cli_mod.app,
        ["daemon-stop", "--pid-file", str(Path("/nonexistent/pid"))],
    )
    assert r.exit_code != 0


def test_daemon_stop_windows_taskkill(tmp_path):
    if sys.platform != "win32":
        pytest.skip("taskkill-specific")
    pid_file = tmp_path / "p.pid"
    pid_file.write_text("4242", encoding="ascii")
    calls: list = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with patch.object(cli_mod.subprocess, "run", fake_run):
        r = CliRunner().invoke(cli_mod.app, ["daemon-stop", "--pid-file", str(pid_file)])
    assert r.exit_code == 0
    # Tree-kill (/T) is required because the pidfile holds the PID of
    # PyInstaller's onefile bootloader, but the actual server is a
    # re-exec'd python grandchild. Killing only the bootloader leaves
    # the grandchild orphaned with the port still bound.
    assert calls, "expected taskkill to be invoked"
    cmd = calls[0]
    assert cmd[0] == "taskkill"
    assert "/T" in cmd, f"missing /T tree-kill flag: {cmd}"
    assert "/F" in cmd, f"missing /F force flag: {cmd}"
    assert "/PID" in cmd and "4242" in cmd, f"missing PID arg: {cmd}"
