"""Omega Runtime Studio — local model control plane and OpenAI-compatible API."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("omega3-portable")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"
