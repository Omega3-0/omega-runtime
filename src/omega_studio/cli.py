"""Typer CLI: serve (foreground) and daemon (background on Windows)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer

from omega_studio.paths import app_data_dir, embedded_python

_PRODUCT = "Omega Runtime Studio"
_DEFAULT_DAEMON_LOG_MAX_BYTES = 10 * 1024 * 1024

app = typer.Typer(
    name="omega3-portable",
    help=f"{_PRODUCT} (CLI — see README «Aliases»)",
    context_settings={"help_option_names": ["-h", "--help"]},
    # Disable Typer's rich-formatted exception handler. When the daemon
    # spawns a child without a console (DETACHED_PROCESS), rich's
    # markdown_it import chain can itself raise — masking the REAL
    # exception with "Error in sys.excepthook" and a meaningless trace
    # ending in typer/rich_utils.py. Plain Python traceback shows the
    # actual error (e.g. _sqlite3 missing, port already bound, etc.)
    # in the daemon log where the operator can act on it.
    pretty_exceptions_enable=False,
)


def _rotate_log_if_needed(log_path: Path, *, max_bytes: int) -> Path | None:
    """Rotate ``daemon.log`` at startup when it has grown beyond *max_bytes*."""
    if max_bytes <= 0 or not log_path.is_file():
        return None
    try:
        if log_path.stat().st_size <= max_bytes:
            return None
    except OSError:
        return None
    rotated = log_path.with_suffix(log_path.suffix + ".1")
    try:
        if rotated.exists():
            rotated.unlink()
        log_path.replace(rotated)
    except OSError:
        return None
    return rotated


def _maybe_reexec_with_embedded_for_serve(argv: list[str] | None = None) -> None:
    """Frozen one-file server: re-exec with sidecar python for full stdlib + site-packages."""
    if not getattr(sys, "frozen", False):
        return
    if os.environ.get("OMEGA_SKIP_EMBEDDED_REEXEC"):
        return
    py = embedded_python()
    if py is None:
        return
    try:
        if Path(sys.executable).resolve() == py.resolve():
            return
    except OSError:
        return
    use_argv = argv if argv is not None else sys.argv
    new_argv = [str(py), "-m", "omega_studio.cli"] + use_argv[1:]
    os.execv(str(py), new_argv)


def _prepare_env(
    *,
    host: Optional[str] = None,
    port: Optional[int] = None,
    bundle: Optional[str] = None,
    n_ctx: Optional[int] = None,
    n_gpu_layers: Optional[int] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
) -> None:
    if host:
        os.environ["OMEGA_STUDIO_HOST"] = host
    if port is not None:
        os.environ["OMEGA_STUDIO_PORT"] = str(port)
    if bundle:
        os.environ["OMEGA_BUNDLE_ROOT"] = str(Path(bundle).resolve())
    if n_ctx is not None:
        os.environ["OMEGA_GLOBAL_N_CTX"] = str(int(n_ctx))
    if n_gpu_layers is not None:
        os.environ["OMEGA_GLOBAL_N_GPU_LAYERS"] = str(int(n_gpu_layers))
    if temperature is not None:
        os.environ["OMEGA_GLOBAL_TEMPERATURE"] = str(float(temperature))
    if top_p is not None:
        os.environ["OMEGA_GLOBAL_TOP_P"] = str(float(top_p))


@app.command("serve")
def serve_cmd(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address (LM Studio style)"),
    port: int = typer.Option(11434, "--port", help="Listen port (OpenAI-compatible)"),
    bundle_root_opt: Optional[str] = typer.Option(
        None,
        "--bundle-root",
        help="OMEGA_BUNDLE_ROOT — directory containing vendor/ (accelerator DLL layout)",
    ),
    n_ctx: Optional[int] = typer.Option(None, "--n-ctx"),
    n_gpu_layers: Optional[int] = typer.Option(None, "--n-gpu-layers"),
    temperature: Optional[float] = typer.Option(None, "--temperature"),
    top_p: Optional[float] = typer.Option(None, "--top-p"),
    log_level: str = typer.Option("info", "--log-level", help="Uvicorn log level"),
    reload: bool = typer.Option(False, "--reload", help="Uvicorn reload (dev)"),
):
    """Run API server in the foreground."""
    import uvicorn

    _maybe_reexec_with_embedded_for_serve()

    _prepare_env(
        host=host,
        port=port,
        bundle=bundle_root_opt,
        n_ctx=n_ctx,
        n_gpu_layers=n_gpu_layers,
        temperature=temperature,
        top_p=top_p,
    )
    os.environ.setdefault("OMEGA_STUDIO_HOST", host)
    os.environ.setdefault("OMEGA_STUDIO_PORT", str(port))

    from omega_studio.server.app import app as fastapi_app

    uvicorn.run(
        fastapi_app,
        host=host,
        port=port,
        reload=reload,
        log_level=log_level,
    )


@app.command("daemon")
def daemon_cmd(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address"),
    port: int = typer.Option(11434, "--port", help="Listen port"),
    bundle_root_opt: Optional[str] = typer.Option(None, "--bundle-root"),
    n_ctx: Optional[int] = typer.Option(None, "--n-ctx"),
    n_gpu_layers: Optional[int] = typer.Option(None, "--n-gpu-layers"),
    temperature: Optional[float] = typer.Option(None, "--temperature"),
    top_p: Optional[float] = typer.Option(None, "--top-p"),
    log_file: Optional[str] = typer.Option(
        None,
        "--log-file",
        help="Append logs here (default: app data / daemon.log)",
    ),
    pid_file: Optional[str] = typer.Option(
        None,
        "--pid-file",
        help="Write PID here after spawn",
    ),
):
    """Headless server: subprocess with CREATE_NO_WINDOW on Windows (no tray)."""
    _prepare_env(
        host=host,
        port=port,
        bundle=bundle_root_opt,
        n_ctx=n_ctx,
        n_gpu_layers=n_gpu_layers,
        temperature=temperature,
        top_p=top_p,
    )
    os.environ["OMEGA_STUDIO_HOST"] = host
    os.environ["OMEGA_STUDIO_PORT"] = str(port)

    log_path = Path(log_file or app_data_dir() / "daemon.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    max_log_bytes = int(os.environ.get("OMEGA_DAEMON_LOG_MAX_BYTES", _DEFAULT_DAEMON_LOG_MAX_BYTES))
    _rotate_log_if_needed(log_path, max_bytes=max_log_bytes)

    if getattr(sys, "frozen", False):
        embed = embedded_python()
        if embed is not None:
            cmd = [
                str(embed),
                "-m",
                "omega_studio.cli",
                "serve",
                "--host",
                host,
                "--port",
                str(port),
                "--log-level",
                "info",
            ]
            if bundle_root_opt:
                cmd.extend(["--bundle-root", bundle_root_opt])
        else:
            cmd = [
                str(Path(sys.executable).resolve()),
                "serve",
                "--host",
                host,
                "--port",
                str(port),
                "--log-level",
                "info",
            ]
            if bundle_root_opt:
                cmd.extend(["--bundle-root", bundle_root_opt])
    else:
        exe = embedded_python() or Path(sys.executable)
        cmd = [
            str(exe),
            "-m",
            "uvicorn",
            "omega_studio.server.app:app",
            "--host",
            host,
            "--port",
            str(port),
            "--log-level",
            "info",
        ]

    kwargs: dict = {}
    if sys.platform == "win32":
        # CREATE_NO_WINDOW alone gives us "no console flash" without a
        # full detach; CREATE_NEW_PROCESS_GROUP keeps the child immune
        # to the parent's Ctrl+C. We deliberately AVOID DETACHED_PROCESS
        # — it interacts badly with the PyInstaller onefile→onefile
        # spawn pattern (see env-scrub below).
        kwargs["creationflags"] = (
            subprocess.CREATE_NO_WINDOW
            | subprocess.CREATE_NEW_PROCESS_GROUP
        )
        kwargs["close_fds"] = True
    log_f = open(log_path, "a", encoding="utf-8")
    # Scrub PyInstaller's parent→child onefile-coordination env vars.
    #
    # PyInstaller onefile bootloader uses `_MEIPASS2` to tell a
    # re-exec'd self that "you don't need to re-extract; reuse this
    # dir". When `Server.exe daemon` spawns `Server.exe serve`, the
    # child inherits the parent's `_MEIPASS2=<parent_MEI_temp_dir>`
    # and the child's bootloader honors it — using the PARENT's temp
    # dir as its own _MEIPASS. Then the parent exits, its atexit
    # cleanup deletes that temp dir, and the child can no longer
    # find _sqlite3.pyd / sqlite3.dll mid-import — crashing with
    # `ModuleNotFoundError: No module named '_sqlite3'` (or `DLL load
    # failed`, depending on which file goes missing first).
    #
    # The fix: strip `_MEIPASS2` (and the broader `_PYI*` family
    # PyInstaller uses for bootloader handoff) so the child performs
    # a fresh extraction to its OWN `_MEI<child_pid>` — independent
    # of the parent's lifecycle.
    child_env = os.environ.copy()
    for var in list(child_env.keys()):
        if var == "_MEIPASS2" or var.startswith("_PYI"):
            child_env.pop(var, None)
    proc = subprocess.Popen(
        cmd,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=child_env,
        **kwargs,
    )
    if pid_file:
        pid_path = Path(pid_file)
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(str(proc.pid), encoding="utf-8")
    typer.echo(f"daemon pid={proc.pid} log={log_path}")


@app.command("daemon-stop")
def daemon_stop_cmd(
    pid_file: Optional[str] = typer.Option(
        None,
        "--pid-file",
        help="PID file written by `omega3-portable daemon --pid-file …`",
    ),
    pid: Optional[int] = typer.Option(
        None,
        "--pid",
        help="Process ID (alternative to --pid-file)",
    ),
):
    """Stop a background daemon started with ``daemon --pid-file`` (Windows: taskkill /F)."""
    raw: Optional[int] = None
    if pid is not None:
        raw = int(pid)
    elif pid_file:
        p = Path(pid_file)
        if not p.is_file():
            typer.echo(f"error: pid file not found: {p}", err=True)
            raise typer.Exit(1)
        raw = int(p.read_text(encoding="utf-8").strip())
    else:
        typer.echo("error: pass --pid-file or --pid", err=True)
        raise typer.Exit(1)

    if sys.platform == "win32":
        # /T = tree kill. The pidfile stores the PID PyInstaller's
        # onefile bootloader returned, but that process IMMEDIATELY
        # re-execs python as a grandchild and waits on it; the
        # grandchild is what actually owns the listening socket.
        # Killing the bootloader alone leaves the grandchild orphaned
        # — port stays bound, operators see "address in use" on next
        # restart. /T sweeps the whole subtree.
        r = subprocess.run(
            ["taskkill", "/T", "/PID", str(raw), "/F"],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            typer.echo(f"taskkill exit {r.returncode}: {r.stderr or r.stdout}", err=True)
            raise typer.Exit(r.returncode or 1)
    else:
        import signal

        try:
            os.kill(raw, signal.SIGTERM)
        except ProcessLookupError:
            typer.echo(f"error: no process {raw}", err=True)
            raise typer.Exit(1)
    typer.echo(f"stopped pid={raw}")


def main():
    app()


if __name__ == "__main__":
    main()
