"""Afterglow（续温）—— 把曾经对你好的话，续成往后的陪伴。"""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _resolve_version() -> str:
    version_file = Path(__file__).resolve().parents[2] / "VERSION"
    try:
        return version_file.read_text(encoding="utf-8").strip()
    except OSError:
        try:
            return version("xuwen")
        except PackageNotFoundError:
            return "0.0.0+unknown"


__version__ = _resolve_version()
__all__ = ["__version__"]
