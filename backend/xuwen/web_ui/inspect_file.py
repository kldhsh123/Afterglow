"""导入文件嗅探：文件解码后将身份识别委派给对应 ingestion plugin。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from xuwen.error_i18n import error_text
from xuwen.ingestion.parser import detect_plugin, load_qq_json
from xuwen.ingestion.plugins import InspectableImportPlugin


@dataclass
class IdentityCandidate:
    name: str
    uid: str
    role_hint: Literal["self", "friend", "unknown"]


@dataclass
class InspectResult:
    format: str
    format_label: str
    candidates: list[IdentityCandidate]
    total_messages: int
    error: str = ""


def inspect_chat_file(path: Path) -> InspectResult:
    try:
        payload = load_qq_json(path)
        plugin = detect_plugin(payload)
        if plugin is None:
            return InspectResult(
                format="unknown",
                format_label=error_text("Unknown format"),
                candidates=[],
                total_messages=0,
                error=error_text("Unknown format: no import plugin recognizes this chat file."),
            )
        if not isinstance(plugin, InspectableImportPlugin):
            return InspectResult(
                format="unknown",
                format_label=error_text("Unknown format"),
                candidates=[],
                total_messages=0,
                error=error_text(
                    "Plugin {name} does not support identity inspection.", name=plugin.display_name
                ),
            )
        inspection = plugin.inspect(payload)
        return InspectResult(
            format=inspection.format,
            format_label=inspection.format_label or plugin.display_name,
            candidates=[
                IdentityCandidate(candidate.name, candidate.uid, candidate.role_hint)
                for candidate in inspection.candidates
            ],
            total_messages=inspection.total_messages,
            error=inspection.error,
        )
    except Exception as e:
        return InspectResult(
            format="unknown",
            format_label=error_text("Unknown format"),
            candidates=[],
            total_messages=0,
            error=str(e),
        )
