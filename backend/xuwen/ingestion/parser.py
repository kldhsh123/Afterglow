"""通用聊天导入入口：只负责文件解码和 plugin 调度。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from xuwen.config import Settings
from xuwen.core.errors import ParseError
from xuwen.core.models import NormalizedMessage
from xuwen.error_i18n import error_text
from xuwen.ingestion.plugins import (
    JSONL_RECORDS_KEY,
    TEXT_CONTENT_KEY,
    TEXT_SUFFIX_KEY,
    ImportPlugin,
    list_plugins,
    register_plugin,
    select_plugin,
)
from xuwen.ingestion.plugins.afterglow_v1 import AfterglowV1Plugin
from xuwen.ingestion.plugins.discord_chat_exporter import DiscordChatExporterPlugin
from xuwen.ingestion.plugins.douyin_chat_export import DouyinChatExportPlugin
from xuwen.ingestion.plugins.qqexporter_v5 import QQExporterV5Plugin
from xuwen.ingestion.plugins.telegram_html import TelegramHTMLPlugin
from xuwen.ingestion.plugins.wechat_weflow import WeChatWeFlowPlugin
from xuwen.ingestion.plugins.whatsapp import WhatsAppPlugin

register_plugin(AfterglowV1Plugin())
register_plugin(QQExporterV5Plugin())
register_plugin(WeChatWeFlowPlugin())
register_plugin(DouyinChatExportPlugin())
register_plugin(WhatsAppPlugin())
register_plugin(TelegramHTMLPlugin())
register_plugin(DiscordChatExporterPlugin())


def load_qq_json(path: str | Path) -> dict[str, Any]:
    """解码聊天文件；JSON、JSONL 和文本格式的解释完全交给 plugin。"""
    p = Path(path)
    if not p.exists():
        raise ParseError(error_text("Chat file not found: {p0}", p0=p))
    if not p.is_file():
        raise ParseError(error_text("Select a chat file inside the export directory: {p0}", p0=p))
    if p.suffix.lower() in {".txt", ".md", ".html", ".htm"}:
        try:
            return {
                TEXT_CONTENT_KEY: p.read_text(encoding="utf-8-sig"),
                TEXT_SUFFIX_KEY: p.suffix.lower(),
            }
        except (OSError, UnicodeDecodeError) as e:
            raise ParseError(error_text("Cannot read the UTF-8 chat file: {p0}", p0=p.name)) from e
    if p.suffix.lower() in {".jsonl", ".ndjson"}:
        return _load_jsonl_records(p)
    try:
        with p.open("r", encoding="utf-8-sig") as f:
            payload = json.load(f)
    except json.JSONDecodeError as e:
        raise ParseError(error_text("JSON parsing failed: {p0}", p0=e)) from e
    if not isinstance(payload, dict):
        raise ParseError(error_text("The JSON root must be an object"))
    return cast(dict[str, Any], payload)


def _load_jsonl_records(path: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    # 同一路径重复导入保持稳定，同时隔离不同目录下的同名 JSONL。
    source_namespace = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:12]
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            for line_no, raw_line in enumerate(f, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as e:
                    raise ParseError(
                        error_text("Failed to parse JSONL line {p0}: {p1}", p0=line_no, p1=e.msg)
                    ) from e
                if not isinstance(item, dict):
                    raise ParseError(
                        error_text("JSONL line {p0} must be a JSON object", p0=line_no)
                    )
                item.setdefault(
                    "_jsonlSourceId",
                    f"{path.stem}-{source_namespace}-{line_no}",
                )
                records.append(item)
    except UnicodeDecodeError as e:
        raise ParseError(error_text("The JSONL file is not valid UTF-8: {p0}", p0=e)) from e
    if not records:
        raise ParseError(error_text("The JSONL file contains no records"))
    return {JSONL_RECORDS_KEY: records}


def parse_messages(
    payload: dict[str, Any],
    settings: Settings,
    *,
    plugin_name: str | None = None,
) -> list[NormalizedMessage]:
    plugin = select_plugin(payload, preferred=plugin_name)
    return plugin.parse(payload, settings)


def detect_plugin(payload: dict[str, Any]) -> ImportPlugin | None:
    for plugin in list_plugins():
        try:
            if plugin.match(payload):
                return plugin
        except Exception:
            continue
    return None
