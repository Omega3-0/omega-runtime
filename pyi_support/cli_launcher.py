"""CLI entry for PyInstaller companion exe (serve/daemon).

PyInstaller bootloader normally sets up the DLL search path so that
``_sqlite3.pyd`` can find its sister ``sqlite3.dll`` (both are
extracted into ``sys._MEIPASS`` for onefile builds, or live next to
the .exe in ``_internal/`` for onedir). Under specific spawn
conditions — especially when the parent Server.exe is itself a
PyInstaller bundle that subprocess-spawns ANOTHER Server.exe with
DETACHED_PROCESS + CREATE_NO_WINDOW (the daemon spawn pattern) —
the child's DLL search inheritance can fail in a way that makes
``import _sqlite3`` raise ``DLL load failed`` mid-import. The
parent's add-dll-directory state is per-process and doesn't
propagate to the child.

Belt-and-braces fix: re-call ``os.add_dll_directory(_MEIPASS)``
(and the ``_internal`` next to ``sys.executable`` for onedir) at
launcher entry, BEFORE ANY OTHER IMPORT. Doing it here guarantees
the search path is set up regardless of how the bundle was
invoked. Without this, daemon-spawned children intermittently 500
on the very first sqlite-touching import.
"""

import os
import sys
from pathlib import Path


def _ensure_dll_search_path() -> None:
    if os.name != "nt":
        return
    candidates: list[Path] = []
    # Onefile build: PyInstaller extracts to sys._MEIPASS at runtime.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass))
    # Onedir build (or beside-the-exe layout): _internal/ holds the DLLs.
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir)
        candidates.append(exe_dir / "_internal")
    for c in candidates:
        try:
            if c.is_dir():
                os.add_dll_directory(str(c))
        except (OSError, AttributeError):
            # add_dll_directory missing on very old Python (pre-3.8) or
            # path failed validation — fall through; the bootloader
            # likely set up enough already.
            pass


_ensure_dll_search_path()


from omega_studio.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
