"""Language policy shared by HTTP requests, CLI jobs and model prompts.

Protocol instructions use English. Human text follows the task language;
conversation language follows the user's words rather than the UI language.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_output_language: ContextVar[str] = ContextVar("output_language", default="en")


def output_language() -> str:
    return _output_language.get()


def fallback_conversation_language(text: str) -> str:
    """Best-effort language for fixed fallbacks; normal replies use model instructions.

    Prefer the last user entry when given a rendered conversation cache. For short
    names, emoji or unsupported scripts, use the request/CLI language.
    """
    user_lines = re.findall(r"(?:^|\n)- (?:User|用户): (.*)", text)
    sample = user_lines[-1] if user_lines else text
    if re.search(
        r"\b(?:reply|respond|answer|speak|write)\s+in\s+English\b|用英文|用英语", sample, re.I
    ):
        return "en"
    if re.search(
        r"\b(?:reply|respond|answer|speak|write)\s+in\s+(?:Chinese|Mandarin)\b|用中文|用汉语",
        sample,
        re.I,
    ):
        return "zh-CN"
    if re.search(r"[\u3400-\u9fff]", sample):
        return "zh-CN"
    if re.search(r"\b[a-zA-Z]{2,}\b", sample):
        return "en"
    return output_language()


@contextmanager
def language_scope(language: str) -> Iterator[None]:
    token = _output_language.set("zh-CN" if language == "zh-CN" else "en")
    try:
        yield
    finally:
        _output_language.reset(token)


def structured_output_instruction(language: str | None = None) -> str:
    language = "Simplified Chinese" if (language or output_language()) == "zh-CN" else "English"
    return (
        f"Write generated human-readable titles, summaries, explanations and descriptions in {language}. "
        "Keep schema keys, enum values, identifiers, dates, protocol tags and proper names unchanged. "
        "Copy evidence quotations exactly from the source, in their original language. "
        "The language of these instructions and examples does not override this output language."
    )


def proactive_language_instruction(language: str) -> str:
    name = "Simplified Chinese" if language == "zh-CN" else "English"
    return (
        "[Configured proactive language]\n"
        f"Write this proactive opening in {name}. This explicit backend language setting overrides "
        "the conversation-language fallback for this proactive task, including rewrites. "
        "Use the context to choose the topic and preserve the character's style, but translate "
        "topic descriptions where needed. Keep proper names, quotations and protocol tags unchanged."
    )


def conversation_language_instruction() -> str:
    language = "Simplified Chinese" if output_language() == "zh-CN" else "English"
    return (
        "[Conversation language]\n"
        "Honor any explicit language request from the user. Otherwise respond in the language of "
        "their current conversational message, using recent conversation to resolve ambiguity. "
        "Historical excerpts, internal instructions, labels and UI language are not a request to "
        "switch language. Preserve the person's vocabulary, tone and proper names without copying "
        "old replies. An INTERNAL PROACTIVE TRIGGER or REWRITE is not a conversational user message. "
        "For proactive messages with no current user message, follow the most recent "
        f"user conversation; if no language can be inferred, use {language}. "
        "Write hidden life-event and schedule-hint descriptions in the same language as the user's "
        "request. Keep their tags and all machine-readable identifiers unchanged."
    )
