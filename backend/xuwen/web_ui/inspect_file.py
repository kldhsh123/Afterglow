"""导入文件元数据嗅探：只读 JSON 顶部，识别格式和双方候选身份，不解析全量消息。

用于配置向导第 1 步的"从聊天文件识别"按钮：
- 用户选一个文件
- 后端在毫秒级别返回检测到的双方信息（昵称 + UID）
- 用户在 UI 上点哪个是自己、哪个是对方
- 自动填好 SELF_NAME / SELF_UID / FRIEND_NAME / FRIEND_UID

这样小白完全不用打开聊天文件去找那串 u_xxx / wxid_xxx。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


@dataclass
class IdentityCandidate:
    name: str
    uid: str
    role_hint: Literal["self", "friend", "unknown"]


@dataclass
class InspectResult:
    format: Literal["qqexporter_v5", "wechat_weflow", "unknown"]
    candidates: list[IdentityCandidate]
    total_messages: int
    error: str = ""


def inspect_chat_file(path: Path) -> InspectResult:
    """读 JSON 顶部，识别双方候选身份。

    QQ：直接读 chatInfo.{selfUid,selfName}；再扫前若干条 messages 找一个非 self 的 sender 作 friend。
    微信：读 senders 数组；用首条 isSend=1 的 senderID 反查 self。
    """
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    except Exception as e:
        return InspectResult(format="unknown", candidates=[], total_messages=0, error=str(e))

    if _looks_like_qq(data):
        return _inspect_qq(data)
    if _looks_like_wechat(data):
        return _inspect_wechat(data)
    return InspectResult(
        format="unknown",
        candidates=[],
        total_messages=len(data.get("messages") or []),
        error="无法识别格式。请确认是 QQChatExporter V5 或微信 WeFlow 导出的 JSON。",
    )


# ---------- QQ ----------


def _looks_like_qq(data: dict[str, Any]) -> bool:
    info = data.get("chatInfo")
    return isinstance(info, dict) and "selfUid" in info


def _inspect_qq(data: dict[str, Any]) -> InspectResult:
    info = data["chatInfo"]
    self_uid = str(info.get("selfUid") or "")
    self_name = str(info.get("selfName") or info.get("name") or "我")

    candidates: list[IdentityCandidate] = []
    if self_uid:
        candidates.append(
            IdentityCandidate(name=self_name, uid=self_uid, role_hint="self")
        )

    messages = data.get("messages") or []
    seen_uids: set[str] = {self_uid} if self_uid else set()
    # 扫前 200 条找出现的 sender，按频次排序，第一个非 self 作为 friend
    counts: dict[tuple[str, str], int] = {}
    for msg in messages[:200]:
        sender = msg.get("sender") or {}
        uid = str(sender.get("uid") or "")
        name = str(sender.get("name") or sender.get("remark") or "")
        if not uid or uid in seen_uids:
            continue
        key = (uid, name)
        counts[key] = counts.get(key, 0) + 1

    for (uid, name), _ in sorted(counts.items(), key=lambda kv: -kv[1]):
        candidates.append(
            IdentityCandidate(
                name=name or "对方",
                uid=uid,
                role_hint="friend" if len(candidates) == 1 else "unknown",
            )
        )
        if len(candidates) >= 4:
            break

    return InspectResult(
        format="qqexporter_v5",
        candidates=candidates,
        total_messages=len(messages),
    )


# ---------- WeChat WeFlow ----------


def _looks_like_wechat(data: dict[str, Any]) -> bool:
    weflow = data.get("weflow")
    senders = data.get("senders")
    return (
        isinstance(weflow, dict)
        and weflow.get("format") == "arkme-json"
        and isinstance(senders, list)
    )


def _inspect_wechat(data: dict[str, Any]) -> InspectResult:
    senders = data.get("senders") or []
    messages = data.get("messages") or []

    # 用首条 isSend=1 的消息反查 self senderID
    self_sender_id: int | None = None
    for m in messages[:200]:
        if m.get("isSend") == 1:
            sid = m.get("senderID")
            if isinstance(sid, int):
                self_sender_id = sid
                break

    candidates: list[IdentityCandidate] = []
    for s in senders:
        if not isinstance(s, dict):
            continue
        wxid = str(s.get("wxid") or "")
        name = str(s.get("displayName") or "")
        if not wxid:
            continue
        if self_sender_id is not None and s.get("senderID") == self_sender_id:
            role: Literal["self", "friend", "unknown"] = "self"
        elif self_sender_id is not None:
            role = "friend"
        else:
            role = "unknown"
        candidates.append(IdentityCandidate(name=name or wxid, uid=wxid, role_hint=role))

    # self 排第一个，方便前端默认展示
    candidates.sort(key=lambda c: 0 if c.role_hint == "self" else 1)

    return InspectResult(
        format="wechat_weflow",
        candidates=candidates,
        total_messages=len(messages),
    )
