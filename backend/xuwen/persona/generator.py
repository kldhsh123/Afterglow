"""从一个或多个聊天文件生成统一的人格、风格与作息画像。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from xuwen.config import Settings
from xuwen.core.errors import ParseError
from xuwen.core.models import NormalizedMessage, Session
from xuwen.ingestion.cleaner import Cleaner
from xuwen.ingestion.parser import load_qq_json
from xuwen.ingestion.plugins import select_plugin
from xuwen.ingestion.splitter import split_sessions
from xuwen.persona.analyzer import analyze_persona
from xuwen.persona.card import render_persona_card, save_persona_card, save_persona_report
from xuwen.persona.circadian import (
    CIRCADIAN_PROFILE_FILENAME,
    compute_circadian_profile,
    save_circadian_profile,
)
from xuwen.persona.style_profile import build_style_profile, save_style_profile


@dataclass(slots=True)
class PersonaDataset:
    """完成跨文件去重、排序和清洗后的画像输入。"""

    source_files: int
    parsed_messages: int
    duplicate_messages: int
    messages: list[NormalizedMessage]
    sessions: list[Session]


@dataclass(slots=True)
class PersonaGenerationResult:
    """画像生成结果与写入路径。"""

    source_files: int
    parsed_messages: int
    unique_messages: int
    duplicate_messages: int
    friend_messages: int
    sessions: int
    circadian_sample_size: int
    circadian_summary: str
    report_path: Path
    card_path: Path
    style_profile_path: Path
    circadian_path: Path


def load_persona_dataset(
    paths: Sequence[str | Path],
    settings: Settings,
    *,
    plugin_name: str | None = None,
    json_emoji_mode: str = "raw",
) -> PersonaDataset:
    """加载全部画像源，去重后按 plugin 时间线切分会话。"""
    source_paths = [Path(path) for path in paths]
    if not source_paths:
        raise ValueError("至少需要一个聊天 JSON 或 JSONL 文件")

    settings.require_identity()
    parsed_messages = 0
    duplicate_messages = 0
    grouped_messages: dict[str, list[NormalizedMessage]] = {}
    seen: set[tuple[object, ...]] = set()

    for path in source_paths:
        try:
            payload = load_qq_json(path)
            plugin = select_plugin(payload, preferred=plugin_name)
            parsed = plugin.parse(payload, settings)
        except Exception as exc:
            raise ParseError(f"画像源文件处理失败（{path}）：{exc}") from exc

        parsed_messages += len(parsed)
        for message in parsed:
            identity = _message_identity(plugin.name, message)
            if identity in seen:
                duplicate_messages += 1
                continue
            seen.add(identity)
            # persona 不读取 raw；释放逐行 JSON 引用可显著降低多文件画像的峰值内存。
            grouped_messages.setdefault(plugin.name, []).append(
                replace(message, raw=None)
            )

    cleaner = Cleaner(settings, json_emoji_mode=json_emoji_mode)
    cleaned: list[NormalizedMessage] = []
    sessions: list[Session] = []
    for group in grouped_messages.values():
        group.sort(key=_message_sort_key)
        cleaned_group = cleaner.clean_many(group)
        cleaned.extend(cleaned_group)
        sessions.extend(split_sessions(cleaned_group, settings))
    cleaned.sort(key=_message_sort_key)
    sessions.sort(
        key=lambda session: (
            session.start_time_ms,
            session.end_time_ms,
            session.session_id,
        )
    )
    return PersonaDataset(
        source_files=len(source_paths),
        parsed_messages=parsed_messages,
        duplicate_messages=duplicate_messages,
        messages=cleaned,
        sessions=sessions,
    )


def generate_persona_artifacts(
    paths: Sequence[str | Path],
    settings: Settings,
    *,
    out_dir: str | Path | None = None,
    plugin_name: str | None = None,
    json_emoji_mode: str = "raw",
) -> PersonaGenerationResult:
    """基于全部文件生成 persona、风格画像和作息画像。"""
    dataset = load_persona_dataset(
        paths,
        settings,
        plugin_name=plugin_name,
        json_emoji_mode=json_emoji_mode,
    )
    output_dir = Path(out_dir) if out_dir is not None else settings.persona_data_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    report = analyze_persona(
        dataset.sessions,
        friend_name=settings.friend_name,
        self_name=settings.self_name,
        json_emoji_mode=json_emoji_mode,
    )
    style_profile = build_style_profile(
        dataset.sessions,
        friend_name=settings.friend_name,
        self_name=settings.self_name,
    )
    circadian = compute_circadian_profile(dataset.messages)

    report_path = output_dir / "persona_report.json"
    card_path = output_dir / "persona_card.md"
    style_profile_path = output_dir / "persona_style_profile.json"
    circadian_path = output_dir / CIRCADIAN_PROFILE_FILENAME
    save_persona_report(report, report_path)
    save_persona_card(render_persona_card(report), card_path)
    save_style_profile(style_profile, style_profile_path)
    save_circadian_profile(circadian, circadian_path)

    return PersonaGenerationResult(
        source_files=dataset.source_files,
        parsed_messages=dataset.parsed_messages,
        unique_messages=len(dataset.messages),
        duplicate_messages=dataset.duplicate_messages,
        friend_messages=sum(
            1
            for session in dataset.sessions
            for message in session.messages
            if message.is_friend
        ),
        sessions=len(dataset.sessions),
        circadian_sample_size=circadian.sample_size,
        circadian_summary=circadian.summary,
        report_path=report_path,
        card_path=card_path,
        style_profile_path=style_profile_path,
        circadian_path=circadian_path,
    )


def _message_identity(
    plugin_name: str,
    message: NormalizedMessage,
) -> tuple[object, ...]:
    """使用稳定字段去重，同时避免不同平台的短 message id 互相碰撞。"""
    raw = message.raw if isinstance(message.raw, dict) else {}
    has_native_id = any(
        key in raw and raw[key] not in (None, "")
        for key in ("id", "message_id", "messageId", "platformMessageId")
    )
    return (
        plugin_name,
        message.sender_uid,
        message.message_id if has_native_id else "",
        message.timestamp_ms,
        message.raw_type,
        message.text,
        tuple(message.placeholders),
        message.reply_to_id,
    )


def _message_sort_key(message: NormalizedMessage) -> tuple[int, int, str, str]:
    return (
        message.timestamp_ms,
        message.seq,
        message.message_id,
        message.sender_uid,
    )
