"""导入器插件系统。

每个聊天平台的导出格式都不一样。通过 plugin 抽象，让任何贡献者都能添加新的解析器，
不用改 importer 主流程。

写一个 plugin 的最少要求：
    1. 实现 `ImportPlugin` Protocol：name + match(payload) -> bool + parse(payload, settings) -> list[NormalizedMessage]
    2. 按需实现身份嗅探和图片引用能力 Protocol
    3. 在 parser.py 注册（或外部应用调用 `register_plugin()` 注册）
    4. 提交 PR :)

importer 读取 JSON/JSONL 后会按注册顺序遍历 plugins，第一个 `match()` 返回 True 的负责 parse。
用户也可以 `--plugin <name>` 强制指定。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from xuwen.config import Settings
from xuwen.core.errors import ParseError
from xuwen.core.models import NormalizedMessage

JSONL_RECORDS_KEY = "_afterglowJsonlRecords"


def jsonl_records(payload: dict[str, Any]) -> list[dict[str, Any]] | None:
    value = payload.get(JSONL_RECORDS_KEY)
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, dict) for item in value)
    ):
        return None
    return value


@dataclass(slots=True, frozen=True)
class ImportIdentityCandidate:
    name: str
    uid: str
    role_hint: Literal["self", "friend", "unknown"] = "unknown"


@dataclass(slots=True, frozen=True)
class ImportInspection:
    format: str
    candidates: list[ImportIdentityCandidate]
    total_messages: int
    error: str = ""
    format_label: str = ""


@dataclass(slots=True, frozen=True)
class ImportImageRef:
    message_id: str
    image_name: str


@runtime_checkable
class ImportPlugin(Protocol):
    """聊天平台导入插件接口。

    实现要点：
    - 保持纯函数风格，无可变全局状态
    - match 必须便宜，不做 IO；parse 可以重一些但不能在内部做网络请求
    - 输出统一为 NormalizedMessage 列表
    """

    name: str
    """plugin 名称，CLI 使用，需要唯一。"""

    display_name: str
    """人类可读的展示名，比如 'QQChatExporter' / 'WeFlow'。"""

    def match(self, payload: dict[str, Any]) -> bool:
        """判断这个 JSON 或中性 JSONL records 是否能由本插件解析。

        例如：QQ 插件检查 `metadata.name` 是否含 "QQChatExporter"。
        """

    def parse(
        self,
        payload: dict[str, Any],
        settings: Settings,
    ) -> list[NormalizedMessage]:
        """把原始 JSON 转为 NormalizedMessage 列表。"""


@runtime_checkable
class InspectableImportPlugin(Protocol):
    def inspect(self, payload: dict[str, Any]) -> ImportInspection:
        """返回向导所需的格式、身份和消息数。"""


@runtime_checkable
class ImageReferenceImportPlugin(Protocol):
    def extract_image_refs(self, payload: dict[str, Any]) -> list[ImportImageRef]:
        """提取消息 ID 与导出目录内图片路径。"""


# 注册表
_REGISTRY: list[ImportPlugin] = []


def register_plugin(plugin: ImportPlugin) -> None:
    """注册一个 plugin。重名会替换旧的。"""
    global _REGISTRY
    _REGISTRY = [p for p in _REGISTRY if p.name != plugin.name]
    _REGISTRY.append(plugin)


def list_plugins() -> list[ImportPlugin]:
    """返回所有已注册的 plugin（按注册顺序）。"""
    return list(_REGISTRY)


def find_plugin(name: str) -> ImportPlugin | None:
    """按 name 找 plugin。"""
    for p in _REGISTRY:
        if p.name == name:
            return p
    return None


def select_plugin(
    payload: dict[str, Any],
    preferred: str | None = None,
) -> ImportPlugin:
    """选择能处理本 payload 的 plugin。

    优先级：
    1. 用户显式 --plugin 指定的（不再做 match 校验，相信用户）
    2. 第一个 match() 返回 True 的内置 plugin
    """
    if preferred:
        plugin = find_plugin(preferred)
        if plugin is None:
            raise ParseError(
                f"未知的 plugin: {preferred}。可用：{[p.name for p in _REGISTRY]}",
            )
        return plugin

    for plugin in _REGISTRY:
        try:
            if plugin.match(payload):
                return plugin
        except Exception:
            # plugin 自身错误不应阻止其它 plugin
            continue

    raise ParseError(
        "没有 plugin 能识别这份 JSON / JSONL。"
        f"可用 plugins：{[p.name for p in _REGISTRY]}。"
        "如果是新平台导出格式，欢迎在 xuwen/ingestion/plugins/ 下添加新 plugin。",
    )
