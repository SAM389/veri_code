"""VeriCode: verify the code an AI coding agent writes against real tools (PyPI, pyright)."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vericode-ai")
except PackageNotFoundError:  # running from a source checkout without install
    __version__ = "0.0.0"

__all__ = ["__version__"]
