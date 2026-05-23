"""通用 JSON 导入入口。

历史上本文件直接放 QQChatExporter 的 parser；现已重构为 plugin 系统：
- 真正的 parser 在 `xuwen/ingestion/plugins/` 下
- 本模块只负责加载 JSON + 委派给 plugin

保留 `parse_messages()` 顶层 API 不变，方便老调用方平滑过渡。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from xuwen.config import Settings
from xuwen.core.errors import ParseError
from xuwen.core.models import NormalizedMessage
from xuwen.ingestion.plugins import (
    ImportPlugin,
    list_plugins,
    register_plugin,
    select_plugin,
)
from xuwen.ingestion.plugins.qqexporter_v5 import QQExporterV5Plugin
from xuwen.ingestion.plugins.wechat_weflow import WeChatWeFlowPlugin

# 注册内置 plugins（每次 import 都幂等替换）
register_plugin(QQExporterV5Plugin())
register_plugin(WeChatWeFlowPlugin())


def load_qq_json(path: str | Path) -> dict[str, Any]:
    """从磁盘读取导出的 JSON 文件。

    名字里保留 "qq" 是为了兼容老调用，实际可以是任何 plugin 支持的 JSON。
    """
    p = Path(path)
    if not p.exists():
        raise ParseError(f"找不到 JSON 文件：{p}")
    try:
        with p.open("r", encoding="utf-8") as f:
            return cast(dict[str, Any], json.load(f))
    except json.JSONDecodeError as e:
        raise ParseError(f"JSON 解析失败：{e}") from e


def parse_messages(
    payload: dict[str, Any],
    settings: Settings,
    *,
    plugin_name: str | None = None,
) -> list[NormalizedMessage]:
    """把 JSON 转为 NormalizedMessage 列表。

    自动选择匹配的 plugin；可通过 `plugin_name` 强制指定。
    """
    plugin = select_plugin(payload, preferred=plugin_name)
    return plugin.parse(payload, settings)


def detect_plugin(payload: dict[str, Any]) -> ImportPlugin | None:
    """仅识别不解析，返回匹配的 plugin（用于配置向导显示"识别到 XXX 格式"）。"""
    for plugin in list_plugins():
        try:
            if plugin.match(payload):
                return plugin
        except Exception:
            continue
    return None
