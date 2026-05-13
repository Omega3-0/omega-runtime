"""Application data paths and bundle resolution."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False) is True


def bundle_root() -> Path:
    """Directory containing vendor/, models/, etc. Uses OMEGA_BUNDLE_ROOT or exe/layout dir."""
    env = os.environ.get("OMEGA_BUNDLE_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    if _is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def app_data_dir() -> Path:
    """Per-user config + registry (Windows: %LOCALAPPDATA%\\Omega3Portable)."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Omega3Portable"
    return Path.home() / ".local" / "share" / "Omega3Portable"


def ensure_app_dirs() -> tuple[Path, Path]:
    root = app_data_dir()
    models = root / "models"
    root.mkdir(parents=True, exist_ok=True)
    models.mkdir(parents=True, exist_ok=True)
    return root, models


def embedded_python() -> Path | None:
    """Sidecar interpreter for full stdlib next to bundle (see README Production bundle).

    Resolution (frozen): ``<bundle>/python/python.exe``, then
    ``<bundle>/runtime/python/python.exe``, ``<bundle>/embed/python/python.exe``,
    then legacy ``<bundle>/python.exe``.
    Non-frozen: optional ``OMEGA_EMBEDDED_PYTHON`` else ``None`` (callers use ``sys.executable``).
    """
    env = os.environ.get("OMEGA_EMBEDDED_PYTHON")
    if env:
        p = Path(env).expanduser().resolve()
        return p if p.is_file() else None
    root = bundle_root()
    for rel in ("python/python.exe", "runtime/python/python.exe", "embed/python/python.exe"):
        cand = root / rel.replace("/", os.sep)
        if cand.is_file():
            return cand.resolve()
    if _is_frozen():
        leg = Path(sys.executable).resolve().parent / "python.exe"
        if leg.is_file():
            return leg.resolve()
    return None


def portable_server_exe() -> Path | None:
    """Frozen GUI layout: CLI companion next to the GUI exe (PyInstaller bundle)."""
    if not _is_frozen():
        return None
    parent = Path(sys.executable).resolve().parent
    for name in ("Omega3.0-portable-Server.exe", "omega3-portable.exe"):
        cand = parent / name
        if cand.is_file():
            return cand
    return None
