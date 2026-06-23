"""新关系长期记忆。

这层不同于历史聊天 RAG：它记录用户和当前 AI 关系继续发展后产生的新事实。
记忆同时写入 markdown 文件（可读、可备份）和 LanceDB 表（可检索）。
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from xuwen.config import Settings
from xuwen.core.metrics import MetricsRecorder
from xuwen.ingestion.embedder import EmbeddingClient
from xuwen.memory.store import MemoryStore

_IMPORTANT_PATTERNS = (
    "记住", "以后", "下次", "提醒", "别忘", "喜欢", "不喜欢", "讨厌",
    "害怕", "想要", "不要", "别再", "我叫", "我是", "我家", "生日",
    "最近", "明天", "后天", "考试", "面试", "工作", "睡眠", "失眠",
    "熬夜", "睡不着", "还没睡", "睡不下",
)
_GENERIC_SHORTS = {"在吗", "你在干嘛", "你在干什么", "在干嘛", "嗯", "好", "哈哈"}


@dataclass(slots=True, frozen=True)
class RelationshipMemoryEntry:
    text: str
    kind: str = "note"
    importance: int = 1


class RelationshipMemoryManager:
    def __init__(
        self,
        settings: Settings,
        store: MemoryStore,
        embedder: EmbeddingClient,
    ) -> None:
        self.settings = settings
        self.store = store
        self.embedder = embedder
        self.path = settings.persona_data_dir / "relationship_memory.md"

    def load_markdown(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""

    async def relevant_memories(
        self,
        query: str,
        *,
        limit: int = 6,
        metrics: MetricsRecorder | None = None,
        trace_id: str = "",
    ) -> list[str]:
        if not query.strip():
            return []
        try:
            embed_start = time.perf_counter()
            vector = await self.embedder.embed_one(query)
            if metrics is not None:
                metrics.record(
                    "relationship.relevant.embed",
                    (time.perf_counter() - embed_start) * 1000,
                    detail=f"trace={trace_id},query_len={len(query)}",
                )
            search_start = time.perf_counter()
            rows = await self.store.search_relationship_memories(vector, top_k=limit)
            if metrics is not None:
                metrics.record(
                    "relationship.relevant.search",
                    (time.perf_counter() - search_start) * 1000,
                    detail=f"trace={trace_id},rows={len(rows)}",
                )
        except Exception:
            if metrics is not None:
                metrics.record(
                    "relationship.relevant",
                    0.0,
                    error="error",
                    detail=f"trace={trace_id}",
                )
            return []
        out: list[str] = []
        for row in rows:
            text = str(row.get("text") or "").strip()
            if text:
                out.append(text)
        return out[:limit]

    async def render_context(
        self,
        query: str,
        *,
        include_relevant: bool = True,
        metrics: MetricsRecorder | None = None,
        trace_id: str = "",
    ) -> str:
        render_start = time.perf_counter()
        parts: list[str] = []
        markdown_start = time.perf_counter()
        markdown = self.load_markdown()
        if metrics is not None:
            metrics.record(
                "relationship.markdown.read",
                (time.perf_counter() - markdown_start) * 1000,
                detail=f"trace={trace_id},chars={len(markdown)}",
        )
        if markdown:
            parts.append("【关系记忆文件】\n" + markdown)
        relevant: list[str] = []
        if include_relevant:
            relevant = await self.relevant_memories(
                query,
                metrics=metrics,
                trace_id=trace_id,
            )
        if relevant:
            lines = "\n".join(f"- {m}" for m in relevant)
            parts.append("【和当前消息相关的关系记忆】\n" + lines)
        rendered = "\n\n".join(parts)
        if metrics is not None:
            metrics.record(
                "relationship.render",
                (time.perf_counter() - render_start) * 1000,
                detail=(
                    f"trace={trace_id},markdown_chars={len(markdown)},"
                    f"relevant={len(relevant)},"
                    f"include_relevant={include_relevant},"
                    f"rendered_chars={len(rendered)}"
                ),
            )
        return rendered

    async def remember_turn(
        self,
        *,
        conversation_id: str | None,
        user_text: str,
        assistant_text: str,
    ) -> list[RelationshipMemoryEntry]:
        entries = _extract_memory_entries(user_text, assistant_text)
        if not entries:
            return []

        existing = self.load_markdown()
        new_entries = [e for e in entries if e.text not in existing]
        if not new_entries:
            return []

        self._append_markdown(new_entries)
        vectors = await self._embed_entries(new_entries)
        rows: list[dict[str, Any]] = []
        now_ms = int(datetime.now().timestamp() * 1000)
        for entry, vector in zip(new_entries, vectors, strict=True):
            rows.append(
                {
                    "id": _entry_id(entry.text),
                    "vector": vector,
                    "text": entry.text,
                    "kind": entry.kind,
                    "importance": entry.importance,
                    "source": "chat",
                    "conversation_id": conversation_id or "",
                    "created_at_ms": now_ms,
                    "updated_at_ms": now_ms,
                    "deleted": False,
                }
            )
        await self.store.upsert_relationship_memories(rows)
        return new_entries

    def _append_markdown(self, entries: list[RelationshipMemoryEntry]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        existing = self.load_markdown()
        lines: list[str] = []
        if not existing:
            lines.extend(
                [
                    "# 关系记忆",
                    "",
                    "这些是当前关系继续发展后形成的新记忆，优先级高于历史聊天片段。",
                    "",
                ]
            )
        else:
            lines.append(existing)
            lines.append("")
        for entry in entries:
            lines.append(f"- [{today}] ({entry.kind}, {entry.importance}) {entry.text}")
        self.path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    async def _embed_entries(
        self,
        entries: list[RelationshipMemoryEntry],
    ) -> list[list[float]]:
        texts = [e.text for e in entries]
        try:
            return await self.embedder.embed_texts(texts)
        except Exception:
            return [[0.0] * self.settings.embedding_dim for _ in texts]


def _extract_memory_entries(
    user_text: str,
    assistant_text: str,
) -> list[RelationshipMemoryEntry]:
    text = _compact(user_text)
    if not _should_remember(text):
        return []

    kind = _classify(text)
    importance = 2 if kind in {"preference", "boundary", "plan", "rhythm"} else 1
    entry_text = f"用户说：{text}"
    if assistant_text.strip() and kind == "plan":
        entry_text += "。后续可以自然追问这件事。"
    return [RelationshipMemoryEntry(text=entry_text, kind=kind, importance=importance)]


def _should_remember(text: str) -> bool:
    if not text:
        return False
    if text in _GENERIC_SHORTS:
        return False
    if len(text) > 160:
        return False
    return any(p in text for p in _IMPORTANT_PATTERNS)


def _classify(text: str) -> str:
    if any(p in text for p in ("喜欢", "不喜欢", "讨厌", "想要")):
        return "preference"
    if any(p in text for p in ("不要", "别再", "害怕")):
        return "boundary"
    if any(p in text for p in ("明天", "后天", "下次", "提醒", "考试", "面试")):
        return "plan"
    if any(p in text for p in ("睡眠", "失眠", "熬夜", "睡不着", "还没睡", "睡不下")):
        return "rhythm"
    if any(p in text for p in ("我叫", "我是", "生日", "我家")):
        return "fact"
    return "note"


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _entry_id(text: str) -> str:
    digest = hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()[:16]
    return f"rel-{digest}"
