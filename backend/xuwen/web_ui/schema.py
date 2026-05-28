"""字段元数据：从 .env.example 解析分组、说明、字段类型。

设计：
- 字段类型 / 默认值 / 是否 SecretStr 来自 pydantic Settings 模型
- 分组标题 / 字段说明 来自 .env.example 的注释（`# ----- 标题 -----` 和字段前的注释）
- 暴露给前端的字段是白名单：只暴露在 .env.example 中出现过的字段

这样：
- `.env.example` 是字段文案的唯一事实源（开发者改一处即可）
- 高级字段（PII / 切分 / 检索调参）默认不在向导中显示，但仍在 schema 里，留给"高级模式"展开
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic.fields import FieldInfo

from xuwen.config import Settings

FieldType = Literal["string", "secret", "int", "float", "bool", "path", "enum"]


@dataclass
class FieldMeta:
    """暴露给前端的字段元数据。"""

    key: str
    type: FieldType
    group: str
    title: str  # 给小白看的标题；当前默认 = key，可在 _TITLES 表里翻译
    description: str  # 取自 .env.example 注释
    default: str | int | float | bool | None
    required: bool
    secret: bool
    advanced: bool  # 是否属于高级字段（向导默认隐藏）
    choices: list[str] | None = None  # enum 字段的可选值


@dataclass
class SchemaSnapshot:
    """整份 schema 的快照。"""

    groups: list[str] = field(default_factory=list)
    fields: list[FieldMeta] = field(default_factory=list)


# 给小白看的字段标题。未列出的字段沿用 key。
_TITLES: dict[str, str] = {
    "APP_NAME": "应用名称",
    "APP_SLOGAN": "应用副标题",
    "APP_TIMEZONE": "时区",
    "SELF_NAME": "你的昵称",
    "SELF_UID": "你的账号 ID",
    "SELF_ALIASES": "你的别名（可选）",
    "FRIEND_NAME": "朋友的昵称",
    "FRIEND_UID": "朋友的账号 ID",
    "FRIEND_ALIASES": "朋友的别名（可选）",
    "RELATIONSHIP_TYPE": "你们的关系",
    "RELATIONSHIP_DESCRIPTION": "关系描述（自定义关系时填）",
    "OPENAI_API_KEY": "聊天 AI 密钥",
    "OPENAI_BASE_URL": "聊天 AI 接口地址",
    "CHAT_MODEL": "聊天 AI 模型名",
    "EMBEDDING_API_KEY": "向量服务密钥",
    "EMBEDDING_API_URL": "向量服务接口地址",
    "EMBEDDING_MODEL": "向量模型名",
    "EMBEDDING_DIM": "向量维度",
    "XUWEN_API_KEY": "访问本服务的密码",
    "API_AUTH_REQUIRED": "启用 API 鉴权",
    "LABELING_ENABLED": "启用语义打标（推荐）",
    "LABEL_API_KEY": "打标服务密钥",
    "LABEL_API_URL": "打标服务接口地址",
    "LABEL_MODEL": "打标模型名",
    "VISION_ENABLED": "启用看图能力",
    "VISION_API_KEY": "视觉模型密钥",
    "VISION_API_URL": "视觉模型接口地址",
    "VISION_MODEL": "视觉模型名",
    "WEB_ACCESS_ENABLED": "启用联网搜索",
    "WEB_SEARCH_API_KEY": "搜索服务密钥",
    "WEB_SEARCH_PROVIDER": "搜索服务提供商",
    "QUERY_REWRITE_ENABLED": "启用检索改写",
    "RERANK_ENABLED": "启用语义重排",
    "RERANK_MODE": "重排触发模式",
    "CROSS_RERANK_ENABLED": "启用 cross-encoder 粗排",
    "CROSS_RERANK_PROTOCOL": "粗排 API 协议",
    "CHUNKING_STRATEGY": "历史切分策略",
    "ADAPTIVE_CHUNK_MODEL_ENABLED": "启用模型自适应切分",
}

# 高级字段（向导默认折叠）。
_ADVANCED_KEYS: set[str] = {
    "SESSION_GAP_MINUTES",
    "CHUNKING_STRATEGY",
    "WINDOW_SIZE",
    "WINDOW_OVERLAP",
    "SINGLE_CONTEXT_BEFORE",
    "SINGLE_CONTEXT_AFTER",
    "SINGLE_CONTEXT_MAX_CHARS",
    "ADAPTIVE_CHUNK_MODEL_ENABLED",
    "ADAPTIVE_CHUNK_API_URL",
    "ADAPTIVE_CHUNK_API_KEY",
    "ADAPTIVE_CHUNK_MODEL",
    "ADAPTIVE_CHUNK_TEMPERATURE",
    "ADAPTIVE_CHUNK_MAX_TOKENS",
    "ADAPTIVE_CHUNK_MAX_MESSAGES_PER_CALL",
    "ADAPTIVE_CHUNK_TARGET_CHARS",
    "ADAPTIVE_CHUNK_MAX_CHARS",
    "ADAPTIVE_CHUNK_MIN_TURNS",
    "ADAPTIVE_CHUNK_OVERLAP_TURNS",
    "ADAPTIVE_CHUNK_SOFT_GAP_MINUTES",
    "ADAPTIVE_CHUNK_MAX_CONCURRENCY",
    "RESPONSE_PAIR_TOP_K",
    "FRIEND_TOP_K",
    "WINDOW_TOP_K",
    "LIVE_TOP_K",
    "FINAL_CONTEXT_K",
    "RRF_K",
    "RECENCY_HALF_LIFE_DAYS",
    "RECENCY_MAX_BOOST",
    "WARMTH_BOOST",
    "LIVE_SOURCE_WEIGHT",
    "HISTORY_SOURCE_WEIGHT",
    "AI_GENERATED_SOURCE_WEIGHT",
    "AI_GENERATED_LONG_TERM_ENABLED",
    "QUERY_REWRITE_ENABLED",
    "QUERY_REWRITE_API_URL",
    "QUERY_REWRITE_API_KEY",
    "QUERY_REWRITE_MODEL",
    "QUERY_REWRITE_TEMPERATURE",
    "QUERY_REWRITE_MAX_TOKENS",
    "QUERY_REWRITE_MAX_VARIANTS",
    "RERANK_ENABLED",
    "RERANK_MODE",
    "RERANK_API_URL",
    "RERANK_API_KEY",
    "RERANK_MODEL",
    "RERANK_TEMPERATURE",
    "RERANK_MAX_TOKENS",
    "RERANK_TOP_K",
    "RERANK_MIN_CANDIDATES",
    "RERANK_TIMEOUT_SECONDS",
    "RERANK_WEIGHT",
    "RERANK_MAX_SAME_SESSION",
    "CROSS_RERANK_ENABLED",
    "CROSS_RERANK_PROTOCOL",
    "CROSS_RERANK_API_URL",
    "CROSS_RERANK_API_KEY",
    "CROSS_RERANK_MODEL",
    "CROSS_RERANK_INPUT_K",
    "CROSS_RERANK_TOP_N",
    "CROSS_RERANK_TIMEOUT_SECONDS",
    "WRITEBACK_ENABLED",
    "WRITEBACK_QUEUE_SIZE",
    "WRITEBACK_BATCH_TURNS",
    "WRITEBACK_FLUSH_INTERVAL_SECONDS",
    "WRITEBACK_VECTORIZE",
    "LANCE_UPSERT_BATCH_SIZE",
    "EMBEDDING_INPUT_MODE",
    "EMBEDDING_BATCH_SIZE",
    "EMBEDDING_MAX_CONCURRENCY",
    "EMBEDDING_MAX_REQUESTS_PER_MINUTE",
    "EMBEDDING_INCLUDE_ENCODING_FORMAT",
    "EMBEDDING_SEND_DIMENSIONS",
    "EMBEDDING_RETRY_ATTEMPTS",
    "EMBEDDING_RETRY_MAX_WAIT_SECONDS",
    "LABEL_BATCH_SIZE",
    "LABEL_MAX_CONCURRENCY",
    "LABEL_REQUEST_INTERVAL_SECONDS",
    "LABEL_FLUSH_AFTER_N",
    "LABEL_FLUSH_INTERVAL_SECONDS",
    "LABEL_INCREMENTAL",
    "LABEL_MAX_CHARS_PER_MESSAGE",
    "LABEL_MOOD_VOCAB",
    "RESPONSE_POLICY_MODEL_ENABLED",
    "RESPONSE_POLICY_API_URL",
    "RESPONSE_POLICY_API_KEY",
    "RESPONSE_POLICY_MODEL",
    "RESPONSE_POLICY_TEMPERATURE",
    "RESPONSE_POLICY_MAX_TOKENS",
    "SILENCE_RESPONSE_SENTINEL",
    "SILENCE_FINISH_REASON",
    "AI_SILENCE_ENABLED",
    "RESPONSES_STORE_CAPACITY",
    "LIFE_TEMPERATURE",
    "LIFE_MAX_TOKENS",
    "LIFE_UPDATE_INTERVAL_MINUTES",
    "LIFE_MAX_REPLY_DELAY_SECONDS",
    "LIFE_SLEEPING_MIN_REPLY_DELAY_SECONDS",
    "LIFE_MARKER_UPDATE_ENABLED",
    "STICKER_REJECT_RETRY",
    "RESPONSE_STREAMING_ENABLED",
    "UPDATE_CHECK_ENABLED",
    "UPDATE_CHECK_URL",
    "UPDATE_CHECK_TIMEOUT_SECONDS",
    "ENABLE_PII_REDACTION",
    "PII_RULES_PATH",
    "VISION_DESCRIBE_PROMPT",
    "VISION_MAX_IMAGE_BYTES",
    "WEB_SEARCH_BASE_URL",
    "WEB_SEARCH_MAX_RESULTS",
    "WEB_SEARCH_TIMEOUT_SECONDS",
    "WEB_SEARCH_LANGUAGE",
    "WEB_FETCH_ENABLED",
    "WEB_FETCH_TIMEOUT_SECONDS",
    "WEB_FETCH_MAX_URLS",
    "WEB_FETCH_MAX_REDIRECTS",
    "WEB_FETCH_MAX_BYTES",
    "WEB_FETCH_MAX_CHARS",
    "CHAT_MODEL_SUPPORTS_VISION",
    "IMAGE_DATA_DIR",
    "STICKER_DATA_DIR",
    "STICKER_MAX_FOR_AI",
    "STICKER_MAX_IMAGE_BYTES",
    "PERSONA_TEMPLATE",
    "PROMPT_TEMPLATE_DIR",
    "DEBUG_ENDPOINTS_ENABLED",
    "METRICS_CAPACITY",
    "LANCE_DB_PATH",
    "PERSONA_DATA_DIR",
    "CONFIG_UI_ENABLED",
    "CONFIG_UI_PATH_PREFIX",
    "CONFIG_UI_LOCALHOST_ONLY",
    "CONFIG_UI_SETUP_TOKEN",
    "CONFIG_UI_UPLOADS_DIR",
}

# 必填字段（向导第 1-5 步必填）
_REQUIRED_KEYS: set[str] = {
    "SELF_NAME",
    "SELF_UID",
    "FRIEND_NAME",
    "FRIEND_UID",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "CHAT_MODEL",
    "EMBEDDING_API_KEY",
    "EMBEDDING_API_URL",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIM",
    "XUWEN_API_KEY",
}


_SECTION_RE = re.compile(r"^\s*#\s*-+\s*(.+?)\s*-+\s*$")
_KEY_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=")


def parse_env_example(example_path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """从 .env.example 提取 {key: description}（字段前的连续注释行）和 {key: group}。"""
    if not example_path.exists():
        return {}, {}
    text = example_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    desc: dict[str, str] = {}
    group: dict[str, str] = {}
    current_group = "基础设置"
    pending_comments: list[str] = []

    for raw in lines:
        sm = _SECTION_RE.match(raw)
        if sm:
            current_group = sm.group(1).strip()
            pending_comments = []
            continue
        if raw.strip().startswith("#"):
            # 收集字段前的注释（剥掉前导 # 和空格）
            text = raw.lstrip("# ").strip()
            if text:
                pending_comments.append(text)
            continue
        km = _KEY_RE.match(raw)
        if km:
            key = km.group(1)
            if pending_comments:
                desc.setdefault(key, " ".join(pending_comments))
            group.setdefault(key, current_group)
            pending_comments = []
            continue
        # 空行清空缓冲
        if not raw.strip():
            pending_comments = []
    return desc, group


def _infer_field_type(name: str, info: FieldInfo) -> tuple[FieldType, list[str] | None]:
    """从 pydantic FieldInfo 推断给前端用的类型 + 可选值（枚举）。"""
    ann = info.annotation
    # SecretStr / SecretStr | None
    ann_str = str(ann)
    if "SecretStr" in ann_str:
        return "secret", None
    if "Literal[" in ann_str:
        # 提取 Literal 字面量
        m = re.search(r"Literal\[(.+?)\]", ann_str)
        if m:
            raw = m.group(1)
            choices = [x.strip().strip("'\"") for x in raw.split(",")]
            return "enum", choices
    if ann is bool or ann_str.startswith("<class 'bool'"):
        return "bool", None
    if ann is int or "int" in ann_str.split("|")[0]:
        return "int", None
    if ann is float or "float" in ann_str.split("|")[0]:
        return "float", None
    if "Path" in ann_str:
        return "path", None
    return "string", None


def _default_repr(value: object) -> str | int | float | bool | None:
    """把 pydantic 默认值转成 JSON 友好的标量。"""
    if value is None:
        return None
    if isinstance(value, SecretStr):
        v = value.get_secret_value()
        return v if v else None
    if isinstance(value, bool | int | float | str):
        return value
    return str(value)


def build_schema(example_path: Path) -> SchemaSnapshot:
    """构造对外的 schema 快照。"""
    desc_map, group_map = parse_env_example(example_path)
    seen_groups: list[str] = []
    fields: list[FieldMeta] = []

    # 用 Settings 的字段顺序遍历，保证 schema 顺序稳定
    for name, info in Settings.model_fields.items():
        key = name.upper()
        if key not in group_map and key not in _TITLES:
            # 既不在 .env.example 也不在显式标题表里 → 跳过（避免暴露内部字段）
            continue
        ftype, choices = _infer_field_type(name, info)
        group = group_map.get(key, "其它")
        if group not in seen_groups:
            seen_groups.append(group)
        fields.append(
            FieldMeta(
                key=key,
                type=ftype,
                group=group,
                title=_TITLES.get(key, key),
                description=desc_map.get(key, ""),
                default=_default_repr(info.default),
                required=key in _REQUIRED_KEYS,
                secret=ftype == "secret",
                advanced=key in _ADVANCED_KEYS,
                choices=choices,
            )
        )
    return SchemaSnapshot(groups=seen_groups, fields=fields)
