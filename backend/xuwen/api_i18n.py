"""Localized copy for the public HTTP API."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

SUPPORTED_LANGUAGES = ("en", "zh-CN")
DEFAULT_LANGUAGE = "en"

# Fixed English fallbacks keep protocol errors readable even if a locale file is missing.
_FALLBACKS = {
    "auth.config_missing": (
        "API authentication is enabled, but XUWEN_API_KEY is not configured. "
        "Set a local API key in .env first."
    ),
    "auth.invalid": "Missing or invalid API key. Send Authorization: Bearer <key> in the header.",
}


@lru_cache(maxsize=2)
def _load_catalog(language: str) -> dict[str, str]:
    if language not in SUPPORTED_LANGUAGES:
        language = DEFAULT_LANGUAGE
    path = Path(__file__).parent / "locales" / "api" / f"{language}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def request_language(value: str | None, *, default: str = DEFAULT_LANGUAGE) -> str:
    """Choose a supported language by quality, preserving order for equal weights."""
    if value:
        candidates: list[tuple[float, str]] = []
        for raw in value.split(","):
            parts = raw.strip().lower().split(";")
            quality = 1.0
            for parameter in parts[1:]:
                key, _, weight = parameter.strip().partition("=")
                if key == "q":
                    try:
                        quality = float(weight)
                    except ValueError:
                        quality = 0.0
            if 0 < quality <= 1:
                candidates.append((quality, parts[0].strip()))
        for _, language in sorted(candidates, key=lambda item: item[0], reverse=True):
            primary = language.split("-", 1)[0]
            if primary == "zh":
                return "zh-CN"
            if primary == "en":
                return "en"
    return default if default in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def api_text(key: str, language: str = DEFAULT_LANGUAGE) -> str:
    """Resolve API copy with locale-file then fixed English fallback."""
    value = _load_catalog(language).get(key)
    if not isinstance(value, str) or not value:
        value = _load_catalog(DEFAULT_LANGUAGE).get(key) or _FALLBACKS.get(key)
    return value if isinstance(value, str) and value else "Text unavailable"
