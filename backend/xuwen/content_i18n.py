"""Localized generated-content labels with fixed English copy as the fallback."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from xuwen.prompt_language import output_language


@lru_cache(maxsize=2)
def _catalog(language: str) -> dict[str, str]:
    try:
        data = json.loads(
            (Path(__file__).parent / "locales" / "content" / f"{language}.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def content_text(english: str, *, language: str | None = None, **params: object) -> str:
    """The argument is readable English fallback copy, never an opaque key."""
    value = _catalog(language or output_language()).get(english)
    if not isinstance(value, str) or not value.strip():
        value = english
    if not params:
        return value
    try:
        return value.format(**params)
    except (KeyError, IndexError, ValueError, AttributeError, TypeError):
        return english.format(**params)
