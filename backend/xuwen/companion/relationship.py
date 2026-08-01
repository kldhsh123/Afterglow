"""新关系长期记忆。

这层不同于历史聊天 RAG：它记录用户和当前 AI 关系继续发展后产生的新事实。
记忆同时写入 markdown 文件（可读、可备份）和 LanceDB 表（可检索）。
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from xuwen.config import Settings
from xuwen.core.metrics import MetricsRecorder
from xuwen.ingestion.embedder import EmbeddingClient
from xuwen.memory.store import MemoryStore

RelationshipMemoryKind = Literal[
    "preference",
    "boundary",
    "plan",
    "rhythm",
    "fact",
    "relationship",
]


@dataclass(slots=True, frozen=True)
class RelationshipMemoryEntry:
    text: str
    kind: RelationshipMemoryKind
    importance: int


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

    def _read_markdown_raw(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""

    def load_markdown(self) -> str:
        text = self._read_markdown_raw()
        return "\n".join(
            line for line in text.splitlines() if "(note," not in line
        ).strip()

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
            if str(row.get("kind") or "") == "note":
                continue
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
        entry: RelationshipMemoryEntry | None,
    ) -> list[RelationshipMemoryEntry]:
        if entry is None:
            return []
        entries = [entry]

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
        existing = self._read_markdown_raw()
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

def _entry_id(text: str) -> str:
    digest = hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()[:16]
    return f"rel-{digest}"
