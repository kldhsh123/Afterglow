"""导入文件嗅探：文件解码后将身份识别委派给对应 ingestion plugin。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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
    """
    Inspect a chat import file and identify candidate identities.
    
    Parameters:
        path (Path): Path to the chat import file.
    
    Returns:
        InspectResult: Detected format, identity candidates, message count, and any inspection error.
    """
    try:
        payload = load_qq_json(path)
        plugin = detect_plugin(payload)
        if plugin is None:
            return InspectResult(
                format="unknown",
                format_label="未知格式",
                candidates=[],
                total_messages=0,
                error="无法识别格式：没有导入 plugin 能识别该聊天文件。",
            )
        if not isinstance(plugin, InspectableImportPlugin):
            return InspectResult(
                format="unknown",
                format_label="未知格式",
                candidates=[],
                total_messages=0,
                error=f"{plugin.display_name} plugin 未提供身份嗅探能力。",
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
            format_label="未知格式",
            candidates=[],
            total_messages=0,
            error=str(e),
        )
