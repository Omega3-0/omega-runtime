# -*- mode: python ; coding: utf-8 -*-
# PyInstaller — Omega3.0 portable GUI (PySide6). CLI companion: omega_studio_cli.spec
# Build:  set OMEGA_PYI_ONEFILE=1 for --onefile; optional OMEGA_PYI_ICON=path\to\app.ico

import os
import sys
from pathlib import Path

block_cipher = None

SPEC_ROOT = Path(os.path.abspath(SPEC)).parent.resolve()
sys.path.insert(0, str(SPEC_ROOT))

from pyi_support.pyi_common import collect_deps

extras = collect_deps()
ICON = os.environ.get("OMEGA_PYI_ICON") or None
if ICON and not os.path.isfile(ICON):
    ICON = None

ONEFILE = os.environ.get("OMEGA_PYI_ONEFILE", "").strip().lower() in ("1", "true", "yes")

hiddenimports = [
    "omega_studio",
    "omega_studio.gui",
    "omega_studio.gui.main_window",
    "omega_studio.server",
    "omega_studio.server.app",
    "omega_studio.server.routes_v1",
    "omega_studio.server.routes_health",
    "omega_studio.server.routes_hub",
    "omega_studio.server.routes_admin",
    "omega_studio.inference",
    "omega_studio.downloads",
    "omega_studio.downloads.hub_jobs",
    "PySide6",
    "PySide6.QtCore",
    "PySide6.QtWidgets",
    "PySide6.QtGui",
] + extras["hiddenimports"]

a = Analysis(
    [str(SPEC_ROOT / "pyi_support" / "gui_launcher.py")],
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
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

_common_exe_kw = dict(
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name="Omega3.0-portable",
        console=False,
        **_common_exe_kw,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="Omega3.0-portable",
        console=False,
        **_common_exe_kw,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name="Omega3.0-portable",
    )
