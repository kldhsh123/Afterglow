"""主动开场最近上下文文件缓存。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from typing import Any

from xuwen.config import Settings


@dataclass(slots=True, frozen=True)
class ProactiveContextItem:
    role: str
    text: str
    created_at_ms: int
    caller_id: str = ""
    conversation_id: str = ""


class ProactiveContextCache:
    """按 caller/conversation 保存有上限的最近上下文。

    这份缓存刻意和 LanceDB 分开：它不是长期记忆，也不是风格证据，
    只是一小段运行时窗口，用来在主动开场前找安全的话题钩子。
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = settings.persona_data_dir / "proactive_context_cache.json"
        self._lock = asyncio.Lock()

    async def append_turn(
        self,
        *,
        caller_id: str | None,
        conversation_id: str | None,
        user_text: str,
        assistant_text: str,
    ) -> None:
        if not self.settings.proactive_context_cache_enabled:
            return
        keys = _context_keys(caller_id=caller_id, conversation_id=conversation_id)
        if not keys:
            return
        items = self._items_for_turn(
            caller_id=caller_id,
            conversation_id=conversation_id,
            user_text=user_text,
            assistant_text=assistant_text,
        )
        if not items:
            return
        async with self._lock:
            data = self._load()
            by_key = _ensure_by_key(data)
            max_items = max(1, int(self.settings.proactive_context_cache_max_items))
            for key in keys:
                existing = list(by_key.get(key) or [])
                existing.extend(asdict(item) for item in items)
                by_key[key] = existing[-max_items:]
            data["updated_at_ms"] = _now_ms()
            self._save(data)

    async def recent(
        self,
        *,
        caller_id: str | None,
        conversation_id: str | None,
        limit: int | None = None,
    ) -> list[ProactiveContextItem]:
        if not self.settings.proactive_context_cache_enabled:
            return []
        keys = _context_keys(caller_id=caller_id, conversation_id=conversation_id)
        if not keys:
            return []
        async with self._lock:
            data = self._load()
        by_key = _ensure_by_key(data)
        seen: set[tuple[int, str, str]] = set()
        out: list[ProactiveContextItem] = []
        for key in keys:
            for raw in by_key.get(key) or []:
                item = _item_from_raw(raw)
                if item is None:
                    continue
                marker = (item.created_at_ms, item.role, item.text)
                if marker in seen:
                    continue
                seen.add(marker)
                out.append(item)
        out.sort(key=lambda item: item.created_at_ms)
        max_items = max(1, int(limit or self.settings.proactive_context_cache_prompt_items))
        return out[-max_items:]

    def _items_for_turn(
        self,
        *,
        caller_id: str | None,
        conversation_id: str | None,
        user_text: str,
        assistant_text: str,
    ) -> list[ProactiveContextItem]:
        now = _now_ms()
        out: list[ProactiveContextItem] = []
        user = _clean_text(user_text, self.settings.proactive_context_cache_max_text_chars)
        assistant = _clean_text(
            assistant_text,
            self.settings.proactive_context_cache_max_text_chars,
        )
        if user:
            out.append(
                ProactiveContextItem(
                    role="user",
                    text=user,
                    created_at_ms=now,
                    caller_id=caller_id or "",
                    conversation_id=conversation_id or "",
                )
            )
        if assistant:
            out.append(
                ProactiveContextItem(
                    role="assistant",
                    text=assistant,
                    created_at_ms=now + 1,
                    caller_id=caller_id or "",
                    conversation_id=conversation_id or "",
                )
            )
        return out

    def _load(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except FileNotFoundError:
            return {"version": 1, "updated_at_ms": 0, "by_key": {}}
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "updated_at_ms": 0, "by_key": {}}
        if not isinstance(data, dict):
            return {"version": 1, "updated_at_ms": 0, "by_key": {}}
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)


def render_proactive_context_cache(items: list[ProactiveContextItem]) -> str:
    """渲染给 prompt/debug 使用的紧凑时间顺序上下文。"""
    lines: list[str] = []
    for item in items:
        role = "用户" if item.role == "user" else "AI"
        lines.append(f"- {role}: {item.text}")
    return "\n".join(lines)


def _context_keys(*, caller_id: str | None, conversation_id: str | None) -> list[str]:
    keys: list[str] = []
    if caller_id and caller_id.strip():
        keys.append(f"caller:{caller_id.strip()}")
    if conversation_id and conversation_id.strip():
        keys.append(f"conversation:{conversation_id.strip()}")
    return keys


def _clean_text(text: str, max_chars: int) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned or cleaned == "[silent]":
        return ""
    limit = max(20, int(max_chars))
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip() + "..."
    return cleaned


def _ensure_by_key(data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    by_key = data.get("by_key")
    if not isinstance(by_key, dict):
        by_key = {}
        data["by_key"] = by_key
    return by_key


def _item_from_raw(raw: object) -> ProactiveContextItem | None:
    if not isinstance(raw, dict):
        return None
    text = str(raw.get("text") or "").strip()
    role = str(raw.get("role") or "").strip()
    if role not in {"user", "assistant"} or not text:
        return None
    return ProactiveContextItem(
        role=role,
        text=text,
        created_at_ms=int(raw.get("created_at_ms") or 0),
        caller_id=str(raw.get("caller_id") or ""),
        conversation_id=str(raw.get("conversation_id") or ""),
    )


def _now_ms() -> int:
    return int(time.time() * 1000)
