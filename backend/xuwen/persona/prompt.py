"""prompt 模板渲染。

- 加载内置或用户自定义的 Jinja2 模板。
- 把 PersonaCard / 检索结果 / 最近对话渲染成 system prompt。
- 输出最终的 OpenAI chat/completions 兼容的 messages 列表。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import (
    Environment,
    FileSystemLoader,
    PackageLoader,
    Template,
    select_autoescape,
)

from xuwen.config import Settings
from xuwen.core.errors import ConfigError
from xuwen.core.models import RetrievalResult, ScoredChunk
from xuwen.core.time import local_now

# 内置模板目录路径（基于包资源）
_BUILTIN_TEMPLATE_PACKAGE = "xuwen.persona"
_BUILTIN_TEMPLATE_DIR = "templates"

_BUILTIN_TEMPLATES = {"xuwen", "friend", "lover", "family", "colleague"}

_STYLE_GUARD = """

【输出风格硬约束】
1. 不要新增历史片段里没有出现过的 Unicode emoji 或颜文字，例如 🥺、😂、❤️、😭。
2. 不要把 QQ / 聊天导出的占位符转换成 emoji；例如 `[[爱心]]`、`[表情]`、`[图片]` 要么原样使用，要么不用。
3. 只有当相似历史中 {{ friend_name }} 的真实回复，或用户当前输入中明确出现了同一个符号时，才可以复用该符号。
4. 不要把你之前生成过的回复当作 emoji / 颜文字使用依据。
5. 回复要像真实私聊文本，不要为了显得亲密而额外加网络流行表情。
6. 不要输出 `[图片]`、`[语音]`、`[视频]`、`[表情]`、`[[...]]` 等历史占位符；你不能发送历史图片。若要使用已配置表情包，只能输出 `[sticker:名字]`。
7. 除系统明确要求的内部协议块 `<life-update>` / `<schedule-hint>` 外，只输出会真正发送给用户的聊天文字。不要写舞台动作、旁白、拟态动作或身体动作描写，例如“（把手机拿远又凑近）”“*叹气*”“我凑近看看”等；你没有真实身体，也不能假装正在操作手机或摄像头。

【语义贴合硬约束】
1. 相似片段里的 {{ self_name }} 发言只提供语境，不是 {{ friend_name }} 的说话风格；不要复读或改写 {{ self_name }} 的话来冒充 {{ friend_name }}。
2. 如果相似片段主要是 `[图片]`、`[表情]`、`[[...]]` 或只有 {{ self_name }} 的问句，说明证据不足；请短句、克制、自然地回应或追问。
3. 不要主动加入“想你”“有没有想我”“抱抱”“亲亲”“爱你”等亲密内容，除非用户当前明确表达了这类情绪，且相似历史中 {{ friend_name }} 的真实回复也这样说过。
4. 不要因为用户问“在干嘛”“在吗”等寒暄，就推断对方是在想你、撒娇或索取亲密回应。
"""

_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


@dataclass(slots=True)
class ChatMessage:
    """OpenAI 兼容的简化消息。"""

    role: str           # "user" / "assistant" / "system"
    content: str


def build_chat_messages(
    *,
    settings: Settings,
    persona_card: str,
    retrieved: RetrievalResult,
    recent: Iterable[ChatMessage],
    current_user_message: str,
    web_context: str = "",
    url_context: str = "",
) -> list[dict[str, str]]:
    """构造 OpenAI chat/completions 兼容的 messages 列表。

    - 第一条是 system（含 persona 卡片 + 检索结果 + 最近对话）
    - 紧跟着 recent 转过来的多轮对话
    - 最后是 user：current_user_message
    """
    if not current_user_message.strip():
        raise ConfigError("current_user_message 不能为空")

    system_text = _render_system_prompt(
        settings=settings,
        persona_card=persona_card,
        retrieved=retrieved,
        recent=list(recent),
        current_user_message=current_user_message,
        web_context=web_context,
        url_context=url_context,
    )

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_text},
    ]
    for m in recent:
        messages.append({"role": m.role, "content": m.content})
    messages.append({"role": "user", "content": current_user_message})
    return messages


def _render_system_prompt(
    *,
    settings: Settings,
    persona_card: str,
    retrieved: RetrievalResult,
    recent: list[ChatMessage],
    current_user_message: str,
    web_context: str,
    url_context: str,
) -> str:
    template = _load_template(settings)
    now = local_now(settings.app_timezone)
    current_date = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M:%S")
    timezone = now.tzname() or settings.app_timezone
    rendered = template.render(
        app_name=settings.app_name,
        slogan=settings.app_slogan,
        friend_name=settings.friend_name or "TA",
        self_name=settings.self_name or "我",
        relationship_type=settings.relationship_type,
        relationship_description=settings.resolved_relationship_description,
        persona_card=persona_card.strip() if persona_card else "",
        retrieved_friend_examples=_render_friend_examples(
            retrieved.friend_examples,
            settings,
        ),
        retrieved_dialogue_windows=_render_dialogue_windows(retrieved.dialogue_windows),
        recent_conversation=_render_recent(recent, settings),
        current_user_message=current_user_message,
        today=current_date,
        current_date=current_date,
        current_time=current_time,
        current_datetime=now.strftime("%Y-%m-%d %H:%M:%S"),
        current_weekday=_WEEKDAYS[now.weekday()],
        timezone=settings.app_timezone,
        timezone_abbr=timezone,
    ).strip()
    runtime_context = _render_runtime_context(
        settings=settings,
        current_datetime=now.strftime("%Y-%m-%d %H:%M:%S"),
        current_weekday=_WEEKDAYS[now.weekday()],
        timezone=timezone,
        web_context=web_context,
        url_context=url_context,
    )
    return (rendered + "\n\n" + runtime_context + _STYLE_GUARD).strip()


def _load_template(settings: Settings) -> Template:
    """加载模板。

    优先级：
        1. PROMPT_TEMPLATE_DIR 指向的目录中存在 `<persona_template>.md.j2`
        2. persona_template 是绝对路径 → 直接加载
        3. persona_template 是内置名（xuwen / friend / lover / family / colleague）
    """
    name = settings.persona_template

    # 路径形式：绝对路径或 PROMPT_TEMPLATE_DIR 内
    if settings.prompt_template_dir is not None:
        env = Environment(
            loader=FileSystemLoader(str(settings.prompt_template_dir)),
            autoescape=select_autoescape(disabled_extensions=("md.j2",), default=False),
            keep_trailing_newline=True,
        )
        candidate = f"{name}.md.j2"
        try:
            return env.get_template(candidate)
        except Exception:
            # 退到内置名 / 绝对路径处理
            pass

    if Path(name).is_absolute() and Path(name).exists():
        path = Path(name)
        env = Environment(
            loader=FileSystemLoader(str(path.parent)),
            autoescape=select_autoescape(disabled_extensions=("md.j2",), default=False),
            keep_trailing_newline=True,
        )
        return env.get_template(path.name)

    if name in _BUILTIN_TEMPLATES:
        env = Environment(
            loader=PackageLoader(_BUILTIN_TEMPLATE_PACKAGE, _BUILTIN_TEMPLATE_DIR),
            autoescape=select_autoescape(disabled_extensions=("md.j2",), default=False),
            keep_trailing_newline=True,
        )
        return env.get_template(f"{name}.md.j2")

    raise ConfigError(
        f"找不到 persona 模板：{name}。"
        f"内置可选：{sorted(_BUILTIN_TEMPLATES)}；"
        f"或在 .env 设置 PROMPT_TEMPLATE_DIR 指向你自己的模板目录，"
        f"或把 PERSONA_TEMPLATE 设为 *.md.j2 文件的绝对路径。"
    )


def _render_friend_examples(
    chunks: list[ScoredChunk],
    settings: Settings,
    max_items: int = 8,
) -> str:
    """把单条召回片段渲染成提示文本。"""
    if not chunks:
        return ""
    lines: list[str] = []
    for i, c in enumerate(chunks[:max_items], 1):
        if c.kind == "response_pair":
            user_text = str(c.metadata.get("text") or "").strip()
            friend_reply = str(c.metadata.get("friend_reply") or "").strip()
            if not user_text or not friend_reply:
                continue
            when = _human_time(c.timestamp_ms)
            lines.append(
                f"[{i}] {when}\n"
                f"当 {settings.self_name or '用户'} 说：{user_text}\n"
                f"{settings.friend_name or 'TA'} 当时回复：{friend_reply}"
            )
            continue
        # snippet 优先（含上下文），否则用 text
        snippet = (c.metadata.get("dialogue_snippet") or c.text or "").strip()
        if not snippet:
            continue
        when = _human_time(c.timestamp_ms)
        lines.append(f"[{i}] {when}\n{snippet}")
    return "\n\n".join(lines)


def _render_dialogue_windows(chunks: list[ScoredChunk], max_items: int = 4) -> str:
    if not chunks:
        return ""
    lines: list[str] = []
    for i, c in enumerate(chunks[:max_items], 1):
        snippet = c.text.strip()
        if not snippet:
            continue
        when = _human_time(c.timestamp_ms)
        lines.append(f"[{i}] {when}\n{snippet}")
    return "\n\n".join(lines)


def _render_recent(recent: list[ChatMessage], settings: Settings) -> str:
    if not recent:
        return ""
    lines: list[str] = []
    for m in recent:
        speaker = (
            settings.self_name or "我"
            if m.role == "user"
            else (settings.friend_name or "TA")
            if m.role == "assistant"
            else "系统"
        )
        lines.append(f"{speaker}: {m.content}")
    return "\n".join(lines)


def _human_time(ts_ms: int) -> str:
    if not ts_ms:
        return "（时间未知）"
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M")


def _render_runtime_context(
    *,
    settings: Settings,
    current_datetime: str,
    current_weekday: str,
    timezone: str,
    web_context: str,
    url_context: str,
) -> str:
    web_block = web_context.strip()
    url_block = url_context.strip()
    has_web_results = bool(web_block)
    has_url_results = bool(url_block)
    if not has_web_results:
        web_block = "（未提供联网检索结果；不要假装已经查询互联网。）"
    if not has_url_results:
        url_block = "（未提供网页读取结果；不要假装已经打开或读过链接。）"
    web_rule = (
        "联网使用规则：本轮已经提供联网检索结果时，说明用户在询问公开实时信息；"
        "必须优先基于【联网检索结果】概括回答，可以保持私聊语气，"
        "但不要用“我没看新闻”“刚醒”“不知道”“你看到什么了吗”等生活状态来回避。"
        "如果结果不足，只能说“我查到的大概是……”，不要编造结果里没有的细节。"
        if has_web_results
        else (
            "联网使用规则：未提供联网检索结果时，不要假装已经查询互联网；"
            "如果用户明确询问新闻、最新消息、天气、价格等公开实时信息，"
            "应自然说明这边暂时没查到，而不是用生活状态冒充事实。"
        )
    )
    url_rule = (
        "网页读取规则：本轮已经提供网页读取结果时，说明后端实际读取了用户给出的链接；"
        "回答必须基于【网页读取结果】，可以概括、解释或引用少量关键信息，"
        "不要说自己打不开链接。"
        if has_url_results
        else (
            "网页读取规则：未提供网页读取结果时，不要假装已经打开链接；"
            "如果用户要求看链接，应自然说明这边没有读到页面内容。"
        )
    )
    return (
        "【运行时上下文】\n"
        f"- 真实当前时间：{current_datetime} {current_weekday}\n"
        f"- 当前时区：{settings.app_timezone}（{timezone}）\n"
        "- 时间使用规则：用户问今天、现在、刚才、等会儿、睡没睡、吃没吃时，"
        "优先依据这里的真实时间和上面的当前生活状态，不要从历史片段推断今天事实。\n"
        "【联网检索结果】\n"
        f"{web_block}\n"
        f"{web_rule}\n"
        "【网页读取结果】\n"
        f"{url_block}\n"
        f"{url_rule}"
    )


# ---------------------------------------------------------------------------
# 调试辅助
# ---------------------------------------------------------------------------


def dump_prompt(messages: list[dict[str, str]]) -> str:
    """把构造好的 messages 转为可读字符串，方便调试。"""
    return json.dumps(messages, ensure_ascii=False, indent=2)
