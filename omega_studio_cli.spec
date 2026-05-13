# -*- mode: python ; coding: utf-8 -*-
# One-file CLI (Typer: serve / daemon) — output Omega3.0-portable-Server.exe beside the GUI bundle.

import os
import sys
from pathlib import Path

block_cipher = None

SPEC_ROOT = Path(os.path.abspath(SPEC)).parent.resolve()
sys.path.insert(0, str(SPEC_ROOT))

from pyi_support.pyi_common import collect_deps

extras = collect_deps(include_qt_dynamic=False)
ICON = os.environ.get("OMEGA_PYI_ICON") or None
if ICON and not os.path.isfile(ICON):
    ICON = None

hiddenimports = [
    "omega_studio",
    "omega_studio.cli",
    "omega_studio.server",
    "omega_studio.server.app",
    "omega_studio.server.routes_v1",
    "omega_studio.server.routes_health",
    "omega_studio.server.routes_hub",
    "omega_studio.server.routes_admin",
    "omega_studio.inference",
    "omega_studio.registry",
    "omega_studio.downloads",
    "omega_studio.downloads.hub_jobs",
    "omega_studio.resource_manager",
    # SQLite — hub_jobs.py imports `sqlite3` for job persistence; the
    # _sqlite3 C extension is the runtime backing. PyInstaller's static
    # analysis intermittently misses _sqlite3.pyd in the onefile archive
    # — confirmed by daemon-spawned child crashing with
    # "ModuleNotFoundError: No module named '_sqlite3'" while the same
    # bundle invoked directly succeeds. Force-include both so the
    # bundle is reproducible regardless of how it's invoked.
    "sqlite3",
    "sqlite3.dbapi2",
    "_sqlite3",
    # Stdlib modules markdown_it pulls transitively when typer's rich
    # exception handler renders an error. Without these, a real error
    # shows as "Error in sys.excepthook" with a useless trace —
    # masking whatever ACTUALLY went wrong.
    "unicodedata",
    "onnxruntime",
] + extras["hiddenimports"]

a = Analysis(
    [str(SPEC_ROOT / "pyi_support" / "cli_launcher.py")],
    pathex=[str(SPEC_ROOT / "src")],
    binaries=extras["binaries"],
    datas=extras["datas"],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "tkinter",
        "test",
        "unittest",
        "pydoc",
        "xmlrpc",
        "PySide6",
        "PySide6.QtCore",
        "PySide6.QtWidgets",
        "PySide6.QtGui",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="Omega3.0-portable-Server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
)
