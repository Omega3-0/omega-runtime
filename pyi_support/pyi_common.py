"""Shared PyInstaller data/binary/hiddenimport collection for Omega Runtime Studio builds."""

from __future__ import annotations

from typing import Any


def collect_deps(*, include_qt_dynamic: bool = True) -> dict[str, Any]:
    """Merge collect_all / dynamic libs for runtime-heavy packages."""
    datas: list = []
    binaries: list = []
    hiddenimports: list = []

    try:
        from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs
    except ImportError:
        return {"datas": datas, "binaries": binaries, "hiddenimports": hiddenimports}

    for pkg in (
        "uvicorn",
        "fastapi",
        "starlette",
        "pydantic",
        "pydantic_settings",
        "anyio",
        "multipart",
        "httpx",
        "httpcore",
        "h11",
        "typer",
        "click",
        "watchfiles",
        "websockets",
        "yaml",
        # llama_cpp's lib/ holds ctypes-loaded DLLs (llama.dll, ggml*.dll,
        # mtmd.dll). PyInstaller's static analysis can't see ctypes
        # references, so the DLLs would be absent from the bundle even
        # though the Python files make it. collect_all grabs everything
        # in the package dir — turns the bundle from "imports but errors
        # at first generate call" into "actually runs inference".
        "llama_cpp",
    ):
        try:
            d, b, h = collect_all(pkg)
            datas += d
            binaries += b
            hiddenimports += h
        except Exception:
            continue

    dyn_pkgs = ["pydantic_core"]
    if include_qt_dynamic:
        dyn_pkgs.append("PySide6")
    for pkg in dyn_pkgs:
        try:
            binaries += collect_dynamic_libs(pkg)
        except Exception:
            continue

    hiddenimports.extend(
        [
            "uvicorn.logging",
            "uvicorn.loops",
            "uvicorn.loops.auto",
            "uvicorn.protocols.http.auto",
            "uvicorn.protocols.websockets.auto",
            "uvicorn.lifespan.on",
            "pydantic.deprecated.decorator",
        ]
    )

    return {"datas": datas, "binaries": binaries, "hiddenimports": hiddenimports}
