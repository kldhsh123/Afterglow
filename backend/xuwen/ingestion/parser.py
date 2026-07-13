"""通用聊天导入入口：只负责文件解码和 plugin 调度。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from xuwen.config import Settings
from xuwen.core.errors import ParseError
from xuwen.core.models import NormalizedMessage
from xuwen.ingestion.plugins import (
    JSONL_RECORDS_KEY,
    ImportPlugin,
    list_plugins,
    register_plugin,
    select_plugin,
)
from xuwen.ingestion.plugins.afterglow_v1 import AfterglowV1Plugin
from xuwen.ingestion.plugins.qqexporter_v5 import QQExporterV5Plugin
from xuwen.ingestion.plugins.wechat_weflow import WeChatWeFlowPlugin

register_plugin(AfterglowV1Plugin())
register_plugin(QQExporterV5Plugin())
register_plugin(WeChatWeFlowPlugin())


def load_qq_json(path: str | Path) -> dict[str, Any]:
    """读取 JSON 或 JSONL；格式解释完全交给 plugin。"""
    p = Path(path)
    if not p.exists():
        raise ParseError(f"找不到聊天记录文件：{p}")
    if p.suffix.lower() in {".jsonl", ".ndjson"}:
        return _load_jsonl_records(p)
    try:
        with p.open("r", encoding="utf-8-sig") as f:
            payload = json.load(f)
    except json.JSONDecodeError as e:
        raise ParseError(f"JSON 解析失败：{e}") from e
    if not isinstance(payload, dict):
        raise ParseError("JSON 顶层必须是对象")
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
                    raise ParseError(f"JSONL 第 {line_no} 行解析失败：{e.msg}") from e
                if not isinstance(item, dict):
                    raise ParseError(f"JSONL 第 {line_no} 行必须是 JSON 对象")
                item.setdefault(
                    "_jsonlSourceId",
                    f"{path.stem}-{source_namespace}-{line_no}",
                )
                records.append(item)
    except UnicodeDecodeError as e:
        raise ParseError(f"JSONL 不是有效的 UTF-8 文件：{e}") from e
    if not records:
        raise ParseError("JSONL 中没有记录")
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
