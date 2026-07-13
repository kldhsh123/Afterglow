"""WeChat WeFlow（arkme-json / ChatLab JSONL）导入 plugin。

WeFlow 是把微信本地聊天记录导出成 JSON 的工具，与 QQChatExporter 同类但格式不同。

数据结构（顶层）：
    {
        "weflow": {"version": "1.0.3", "format": "arkme-json", "exportedAt": ts},
        "session": {"wxid": "...", "displayName": "...", "type": "私聊"/"群聊", ...},
        "senders": [{"senderID": 1, "wxid": "...", "displayName": "..."}, ...],
        "messages": [...]
    }

每条 message 关键字段：
    {
        "localId": int,
        "createTime": <epoch seconds>,
        "formattedTime": "YYYY-MM-DD HH:MM:SS",
        "type": "文本消息"/"图片消息"/"动画表情"/"引用消息"/"系统消息"/...,
        "localType": int,                # 微信原生 type 编码
        "content": "..."/null,           # 部分类型（图片/语音）为 null
        "isSend": 0/1,                   # 1=自己发出，0=对方
        "senderID": 1/2,                 # 对应 senders 数组下标 ID
        "platformMessageId": "...",
        # 引用消息独有：
        "replyToMessageId": "...",
        "quotedContent": "...",
        "quotedSender": "...",
    }

角色判定优先级：
1. 用 sender 的 wxid 匹配 settings.self_uid / friend_uid（用户已在 .env 配 wxid 形式）；
2. 否则 fallback 用 isSend：1 → self，0 → friend；
3. type 为"系统消息"且 content 不像"撤回了一条消息" → role=system。
"""

from __future__ import annotations

import re
from typing import Any

from xuwen.config import Settings
from xuwen.core.errors import ParseError
from xuwen.core.models import MessageKind, NormalizedMessage, SenderRole
from xuwen.ingestion.plugins import (
    ImportIdentityCandidate,
    ImportImageRef,
    ImportInspection,
    jsonl_records,
)


class WeChatWeFlowPlugin:
    """WeChat WeFlow 的 arkme-json 与 ChatLab JSONL 解析插件。"""

    name = "wechat_weflow"
    display_name = "WeFlow"

    def match(self, payload: dict[str, Any]) -> bool:
        """识别 WeFlow 特征字段。

        允许两种判定方式：
        - 顶层 `weflow` 是 dict 且 format 字段含 'arkme'（最严格）；
        - 同时存在 `session` + `senders` + `messages` 三个 WeFlow 特有结构（兜底）。
        """
        canonical = _canonical_payload(payload)
        if canonical is None:
            return False
        weflow = canonical.get("weflow")
        if isinstance(weflow, dict):
            fmt = str(weflow.get("format") or "").lower()
            if "arkme" in fmt or "weflow" in str(weflow.get("generator") or "").lower():
                return True
        chatlab = canonical.get("chatlab")
        meta = canonical.get("meta")
        if (
            isinstance(chatlab, dict)
            and str(chatlab.get("generator") or "").lower() == "weflow"
            and isinstance(meta, dict)
            and str(meta.get("platform") or "").lower() == "wechat"
            and isinstance(canonical.get("messages"), list)
        ):
            return True
        # 兜底：三个结构同时存在且 senders 是带 wxid 的 list
        if (
            isinstance(canonical.get("session"), dict)
            and isinstance(canonical.get("senders"), list)
            and isinstance(canonical.get("messages"), list)
        ):
            senders = canonical["senders"]
            if senders and isinstance(senders[0], dict) and "wxid" in senders[0]:
                return True
        return False

    def parse(
        self,
        payload: dict[str, Any],
        settings: Settings,
    ) -> list[NormalizedMessage]:
        canonical = _canonical_payload(payload)
        if canonical is None:
            raise ParseError("JSONL 不符合 WeFlow ChatLab 格式")
        if _looks_like_chatlab(canonical):
            return _parse_chatlab_messages(canonical, settings)

        raw_messages = canonical.get("messages")
        if not isinstance(raw_messages, list):
            raise ParseError("payload 中缺少 messages 数组")

        sender_index = _build_sender_index(canonical.get("senders"))

        messages: list[NormalizedMessage] = []
        for idx, raw in enumerate(raw_messages):
            if not isinstance(raw, dict):
                continue
            try:
                msg = _parse_one(raw, settings, sender_index, fallback_seq=idx)
            except Exception:
                continue
            if msg is not None:
                messages.append(msg)

        messages.sort(key=lambda m: (m.timestamp_ms, m.seq))
        return messages

    def inspect(self, payload: dict[str, Any]) -> ImportInspection:
        source_is_jsonl = jsonl_records(payload) is not None
        canonical = _canonical_payload(payload)
        if canonical is None:
            return ImportInspection("unknown", [], 0, "无法识别 WeFlow 格式")
        if _looks_like_chatlab(canonical):
            members: dict[str, str] = {}
            for item in canonical.get("members") or []:
                if not isinstance(item, dict):
                    continue
                uid = str(item.get("platformId") or "")
                if uid:
                    members[uid] = str(
                        item.get("groupNickname") or item.get("accountName") or uid
                    )
            for message in canonical.get("messages") or []:
                if not isinstance(message, dict):
                    continue
                uid = str(message.get("sender") or "")
                if uid and uid not in members:
                    members[uid] = str(
                        message.get("groupNickname") or message.get("accountName") or uid
                    )
            return ImportInspection(
                format="weflow_chatlab_jsonl" if source_is_jsonl else "weflow_chatlab",
                candidates=[
                    ImportIdentityCandidate(name=name, uid=uid)
                    for uid, name in members.items()
                ],
                total_messages=len(canonical.get("messages") or []),
                format_label="WeFlow ChatLab JSONL" if source_is_jsonl else "WeFlow ChatLab",
            )

        senders = canonical.get("senders") or []
        messages = canonical.get("messages") or []
        self_sender_id: int | None = None
        for message in messages:
            if isinstance(message, dict) and message.get("isSend") == 1:
                sender_id = message.get("senderID")
                if isinstance(sender_id, int):
                    self_sender_id = sender_id
                    break
        candidates: list[ImportIdentityCandidate] = []
        for sender in senders:
            if not isinstance(sender, dict):
                continue
            uid = str(sender.get("wxid") or "")
            if not uid:
                continue
            role = (
                "self"
                if self_sender_id is not None and sender.get("senderID") == self_sender_id
                else "friend" if self_sender_id is not None else "unknown"
            )
            candidates.append(
                ImportIdentityCandidate(
                    name=str(sender.get("displayName") or uid),
                    uid=uid,
                    role_hint=role,  # type: ignore[arg-type]
                )
            )
        candidates.sort(key=lambda candidate: 0 if candidate.role_hint == "self" else 1)
        return ImportInspection(
            "wechat_weflow",
            candidates,
            len(messages),
            format_label="WeFlow arkme-json",
        )

    def extract_image_refs(self, payload: dict[str, Any]) -> list[ImportImageRef]:
        canonical = _canonical_payload(payload)
        if canonical is None or not _looks_like_chatlab(canonical):
            return []
        refs: list[ImportImageRef] = []
        for idx, raw in enumerate(canonical.get("messages") or []):
            if not isinstance(raw, dict):
                continue
            content = str(raw.get("content") or "").strip()
            if _chatlab_placeholder(_parse_int(raw.get("type"), default=99), content) != "[图片]":
                continue
            message_id = str(
                raw.get("platformMessageId")
                or raw.get("_jsonlSourceId")
                or f"local-{idx}"
            )
            if message_id and content:
                refs.append(ImportImageRef(message_id, content))
        return refs


def _canonical_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    records = jsonl_records(payload)
    if records is None:
        return payload
    first = records[0]
    if first.get("_type") != "header":
        return None
    chatlab = first.get("chatlab")
    meta = first.get("meta")
    if (
        not isinstance(chatlab, dict)
        or str(chatlab.get("generator") or "").lower() != "weflow"
        or not isinstance(meta, dict)
        or str(meta.get("platform") or "").lower() != "wechat"
    ):
        return None
    members: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    for record in records[1:]:
        item = {key: value for key, value in record.items() if key != "_type"}
        if record.get("_type") == "member":
            members.append(item)
        elif record.get("_type") == "message":
            messages.append(item)
        else:
            return None
    return {
        "chatlab": chatlab,
        "meta": meta,
        "members": members,
        "messages": messages,
        "sourceFormat": "weflow_chatlab_jsonl",
    }


def _looks_like_chatlab(payload: dict[str, Any]) -> bool:
    chatlab = payload.get("chatlab")
    meta = payload.get("meta")
    return (
        isinstance(chatlab, dict)
        and str(chatlab.get("generator") or "").lower() == "weflow"
        and isinstance(meta, dict)
        and str(meta.get("platform") or "").lower() == "wechat"
    )


_CHATLAB_PLACEHOLDERS: dict[int, str] = {
    1: "[图片]",
    2: "[语音]",
    3: "[视频]",
    4: "[文件]",
    5: "[表情]",
    8: "[位置]",
    23: "[通话]",
    24: "[小程序]",
    27: "[名片]",
}

_CHATLAB_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_CHATLAB_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def _chatlab_placeholder(chatlab_type: int, content: str) -> str | None:
    normalized = content.replace("\\", "/").split("?", 1)[0].lower()
    suffix = "." + normalized.rsplit(".", 1)[-1] if "." in normalized else ""
    if suffix in _CHATLAB_IMAGE_EXTS:
        return "[图片]"
    if suffix in _CHATLAB_VIDEO_EXTS:
        return "[视频]"
    return _CHATLAB_PLACEHOLDERS.get(chatlab_type)


def _parse_chatlab_messages(
    payload: dict[str, Any],
    settings: Settings,
) -> list[NormalizedMessage]:
    raw_messages = payload.get("messages")
    if not isinstance(raw_messages, list):
        raise ParseError("ChatLab payload 中缺少 messages 数组")

    messages: list[NormalizedMessage] = []
    for idx, raw in enumerate(raw_messages):
        if not isinstance(raw, dict):
            continue
        sender_wxid = str(raw.get("sender") or "")
        sender_name = str(raw.get("groupNickname") or raw.get("accountName") or sender_wxid)
        chatlab_type = _parse_int(raw.get("type"), default=99)
        content = str(raw.get("content") or "").strip()
        content_lower = content.lower()
        recalled = chatlab_type == 80 and any(
            hint.lower() in content_lower for hint in _RECALL_HINTS
        )
        is_system = chatlab_type == 80 and not recalled
        role = _infer_role(
            sender_wxid=sender_wxid,
            is_send=-1,
            settings=settings,
            is_system=is_system,
        )
        reply_id = str(raw.get("replyToMessageId") or "")
        placeholder = _chatlab_placeholder(chatlab_type, content)
        placeholders = [placeholder] if placeholder else []
        is_reply = chatlab_type == 25 or bool(reply_id)
        text_content = _strip_quoted_tail(content) if is_reply else content

        if recalled:
            kind = MessageKind.RECALLED
        elif is_system:
            kind = MessageKind.SYSTEM
        elif is_reply:
            kind = MessageKind.REPLY
        elif chatlab_type in {0, 7, 24, 99} and content and not placeholder:
            kind = MessageKind.TEXT
        elif placeholders:
            kind = MessageKind.PLACEHOLDER
        elif content:
            kind = MessageKind.TEXT
        else:
            kind = MessageKind.UNKNOWN

        timestamp = _parse_int(raw.get("timestamp"), default=0)
        timestamp_ms = timestamp if timestamp >= 1_000_000_000_000 else timestamp * 1000
        messages.append(
            NormalizedMessage(
                message_id=str(
                    raw.get("platformMessageId")
                    or raw.get("_jsonlSourceId")
                    or f"local-{idx}"
                ),
                seq=idx,
                timestamp_ms=timestamp_ms,
                sender_uid=sender_wxid,
                sender_name=sender_name,
                sender_role=role,
                kind=kind,
                raw_type=f"chatlab_{chatlab_type}",
                text="" if placeholder else text_content,
                placeholders=placeholders,
                reply_to_id=reply_id or None,
                reply_to_summary=content[len(text_content) :].strip()[:120] or None,
                recalled=recalled,
                system=is_system,
                has_media=bool(placeholders),
                raw=raw,
            )
        )

    messages.sort(key=lambda message: (message.timestamp_ms, message.seq))
    return messages


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------


# senderID → (wxid, displayName)
_SenderIndex = dict[int, tuple[str, str]]


def _build_sender_index(senders: Any) -> _SenderIndex:
    """从 senders 数组建立 senderID → (wxid, displayName) 索引。

    senders 数组每项形如：
        {"senderID": 1, "wxid": "wxid_...", "displayName": "MC", "nickname": "MC"}
    displayName 缺失时退到 nickname；都没有就用 wxid 自己。
    """
    index: _SenderIndex = {}
    if not isinstance(senders, list):
        return index
    for s in senders:
        if not isinstance(s, dict):
            continue
        sid = _parse_int(s.get("senderID"), default=-1)
        if sid < 0:
            continue
        wxid = str(s.get("wxid") or "")
        display = str(s.get("displayName") or s.get("nickname") or wxid)
        index[sid] = (wxid, display)
    return index


# 撤回提示文案模式（微信 zh-CN）。
# 命中其一即把这条系统消息从 SYSTEM 上调为 RECALLED，给下游统计/清洗一个稳定信号。
_RECALL_HINTS = (
    "撤回了一条消息",
    "撤回了一条信息",
    "已撤回",
    "撤回了消息",
    "recalled a message",
)

# 微信内置 emoji token：`[微笑]` `[捂脸]` `[破涕为笑]` 等（中文 1-4 字 + 方括号）。
# 注意要排除真实占位符 `[图片]` `[位置]` 等，这些虽然形式相同但语义不同。
_WECHAT_EMOJI_RE = re.compile(r"\[[一-龥]{1,4}\]")
_RESERVED_BRACKET_TOKENS: frozenset[str] = frozenset({
    "[图片]", "[语音]", "[视频]", "[文件]", "[位置]", "[链接]", "[通话]",
    "[名片]", "[小程序]", "[转账]", "[红包]", "[表情]", "[表情包]", "[动画表情]",
    "[撤回]", "[系统消息]", "[引用]", "[未知消息]", "[附件]",
})


def _strip_wechat_emoji(text: str) -> str:
    """剥离微信内置 emoji token，保留系统占位符。"""
    return _WECHAT_EMOJI_RE.sub(
        lambda m: "" if m.group(0) not in _RESERVED_BRACKET_TOKENS else m.group(0),
        text,
    )


def _is_emoji_only_text(text: str) -> bool:
    """判断一条文本消息是否完全由微信内置 emoji 组成。

    要求：原文非空，去掉所有 emoji token + 空白后变为空字符串，且未保留任何系统占位符。
    用来识别"单发表情"场景，让下游把它归类为 PLACEHOLDER 而非 TEXT，避免主模型
    把 `[微笑]` 当成真人正文证据。
    """
    if not text:
        return False
    stripped = _strip_wechat_emoji(text)
    return not stripped.strip()


def _parse_one(
    raw: dict[str, Any],
    settings: Settings,
    sender_index: _SenderIndex,
    fallback_seq: int,
) -> NormalizedMessage | None:
    """解析单条 WeFlow 消息。"""
    sender_id = _parse_int(raw.get("senderID"), default=-1)
    sender_wxid, sender_display = sender_index.get(sender_id, ("", ""))

    raw_type = str(raw.get("type") or "")
    is_system_type = raw_type == "系统消息"
    is_send = _parse_int(raw.get("isSend"), default=-1)

    content_field = raw.get("content")
    text_field = str(content_field or "").strip()

    # 撤回：WeFlow 用系统消息表达撤回；命中后改类型，不参与正常 self/friend 统计。
    recalled = is_system_type and any(hint in text_field for hint in _RECALL_HINTS)

    role = _infer_role(
        sender_wxid=sender_wxid,
        is_send=is_send,
        settings=settings,
        is_system=is_system_type and not recalled,
    )

    placeholders = _extract_placeholders(raw_type, raw)
    has_media = bool(placeholders)

    reply_info = _extract_reply(raw)

    # 单发表情：纯文本消息内容完全由 [微笑] 等微信内置 emoji 组成 → 转为 PLACEHOLDER。
    # 避免主模型把字面 `[微笑]` 当真人语气证据；下游 cleaner 也不必再单独兜底。
    # 引用消息保留正文，由 cleaner 统一归一化为 [表情]，不在这里改 kind。
    if (
        raw_type == "文本消息"
        and not reply_info
        and _is_emoji_only_text(text_field)
    ):
        has_media = True
        placeholders = ["[表情]"]
        text_field = ""

    kind = _classify_kind(
        raw_type=raw_type,
        recalled=recalled,
        system=is_system_type and not recalled,
        has_text=bool(text_field),
        has_media=has_media,
        is_reply=reply_info is not None,
    )

    # 引用消息：WeFlow 在 content 里塞了 "正文[引用 X：Y]"；尽量只把正文留下，
    # 把 quoted 信息抽到 reply_to_summary，避免主模型把"[引用 ...]"误当真人语气。
    final_text = _strip_quoted_tail(text_field) if reply_info else text_field

    return NormalizedMessage(
        message_id=str(raw.get("platformMessageId") or f"local-{fallback_seq}"),
        seq=_parse_int(raw.get("localId"), default=fallback_seq),
        timestamp_ms=_parse_int(raw.get("createTime"), default=0) * 1000,
        sender_uid=sender_wxid,
        sender_name=sender_display,
        sender_role=role,
        kind=kind,
        raw_type=raw_type,
        text=final_text,
        placeholders=placeholders,
        reply_to_id=reply_info[0] if reply_info else None,
        reply_to_summary=reply_info[1] if reply_info else None,
        recalled=recalled,
        system=is_system_type and not recalled,
        has_media=has_media,
        raw=raw,
    )


def _infer_role(
    *,
    sender_wxid: str,
    is_send: int,
    settings: Settings,
    is_system: bool,
) -> SenderRole:
    """先用 UID 集合命中，再 isSend 兜底；系统消息单独归类。

    settings.all_self_uids / all_friend_uids 把跨平台 / 跨账号的多个 UID
    合并成一个集合，所以同一个人在 QQ 和微信的两个账号都能被识别为同一身份。
    """
    if is_system:
        return "system"
    # 1) UID 集合优先（要求用户把 wxid 加进 .env 的 self_uid 或 SELF_UIDS）
    if sender_wxid:
        if sender_wxid in settings.all_self_uids:
            return "self"
        if sender_wxid in settings.all_friend_uids:
            return "friend"
    # 2) WeFlow 自带的 isSend 字段足够可靠
    if is_send == 1:
        return "self"
    if is_send == 0:
        return "friend"
    return "other"


# WeFlow type 字符串 → 占位符短标签。
# 占位符只在内部用作"这条非纯文本"的信号，主模型 prompt 里会被适度过滤，
# 不会直接拿来当真人回复证据。
_TYPE_PLACEHOLDER_MAP: dict[str, str] = {
    "图片消息": "[图片]",
    "动画表情": "[表情]",
    "语音消息": "[语音]",
    "视频消息": "[视频]",
    "文件消息": "[文件]",
    "位置消息": "[位置]",
    "链接消息": "[链接]",
    "通话消息": "[通话]",
    "名片消息": "[名片]",
    "小程序消息": "[小程序]",
    "转账消息": "[转账]",
    "红包消息": "[红包]",
}


def _extract_placeholders(raw_type: str, raw: dict[str, Any]) -> list[str]:
    """根据消息 type 给出 [类型] 短标签列表。

    引用消息 / 文本消息 / 系统消息不在此处加占位符，由上层 kind 决定。
    """
    if raw_type in {"文本消息", "引用消息", "系统消息", "私聊", "群聊"}:
        return []
    tag = _TYPE_PLACEHOLDER_MAP.get(raw_type)
    if tag:
        return [tag]
    # 未知非文本类型：用原始 type 兜底，便于后续排查
    if raw_type:
        return [f"[{raw_type}]"]
    return []


def _extract_reply(raw: dict[str, Any]) -> tuple[str, str] | None:
    """提取引用信息。命中条件：存在 replyToMessageId 或 quotedContent。"""
    reply_id = raw.get("replyToMessageId")
    quoted = raw.get("quotedContent")
    sender = raw.get("quotedSender")
    if reply_id or quoted:
        summary_parts: list[str] = []
        if sender:
            summary_parts.append(str(sender))
        if quoted:
            summary_parts.append(str(quoted))
        summary = "：".join(summary_parts)[:120]
        return (str(reply_id or ""), summary)
    return None


# 匹配 WeFlow 在引用消息正文末尾追加的 "[引用 ...：...]"。
# 例如 "1[引用 开朗的火山河123：你在县城吗]" → 提出 "1"。
# 用最后一次出现的 "[引用" 作为切点，避免吃掉正文里合法的方括号。
def _strip_quoted_tail(text: str) -> str:
    marker = "[引用"
    idx = text.rfind(marker)
    if idx < 0:
        return text
    # 必须以 "]" 收尾才认为是 WeFlow 自带的引用尾巴，否则保持原文
    if not text.rstrip().endswith("]"):
        return text
    return text[:idx].rstrip()


def _classify_kind(
    *,
    raw_type: str,
    recalled: bool,
    system: bool,
    has_text: bool,
    has_media: bool,
    is_reply: bool,
) -> MessageKind:
    if recalled:
        return MessageKind.RECALLED
    if system:
        return MessageKind.SYSTEM
    if is_reply:
        return MessageKind.REPLY
    if has_text:
        return MessageKind.TEXT
    if has_media:
        return MessageKind.PLACEHOLDER
    return MessageKind.UNKNOWN


def _parse_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default
