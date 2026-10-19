"""Localized application errors with readable, fixed English fallbacks."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from xuwen.prompt_language import output_language


@lru_cache(maxsize=2)
def _catalog(language: str) -> dict[str, str]:
    language = "zh-CN" if language == "zh-CN" else "en"
    try:
        data = json.loads(
            (Path(__file__).parent / "locales" / "errors" / f"{language}.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError, UnicodeError):
        return {}
    return data if isinstance(data, dict) else {}


def error_text(english: str, **params: object) -> str:
    """Translate owned copy only; upstream diagnostics and user data remain intact.

    Missing catalogs, keys or malformed translations fall back to the fixed source
    message so reporting a failure never depends on a complete translation file.
    """
    value = _catalog(output_language()).get(english)
    if isinstance(value, str) and value:
        try:
            return value.format(**params)
        except (KeyError, IndexError, ValueError, AttributeError, TypeError):
            pass
    return english.format(**params)
