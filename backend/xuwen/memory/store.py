"""LanceDB 封装：连接、建表、增量 upsert、检索、统计。

设计：
- 单写 worker 模式：内部用 asyncio.Lock 串行化所有写操作，避免 LanceDB 并发写冲突。
- 检索是只读的，可以并发。
- 软删除：retriever 默认通过 where="deleted = false" 过滤。
- where 表达式中的字符串值必须经过 `_quote_lance` 转义，防止 SQL 注入。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import lancedb
import numpy as np
import pyarrow as pa
from lancedb.db import DBConnection
from lancedb.table import Table

from xuwen.config import Settings
from xuwen.core.errors import StoreError
from xuwen.core.models import DialogueWindowChunk, FriendMessageChunk, ResponsePairChunk
from xuwen.core.time import now_ms
from xuwen.memory.schema import (
    TABLE_DIALOGUE_WINDOWS,
    TABLE_FRIEND_MESSAGES,
    TABLE_LIVE_MESSAGES,
    TABLE_RELATIONSHIP_MEMORIES,
    TABLE_RESPONSE_PAIRS,
    dialogue_windows_schema,
    friend_messages_schema,
    live_messages_schema,
    relationship_memories_schema,
    response_pairs_schema,
)

logger = logging.getLogger(__name__)

# 一次 merge_insert 的默认最大行数，避免 1w 条 ×4096 维一次性入内存
_UPSERT_BATCH_SIZE = 128
_DB_PERF_LAST_LIMIT = 80


@dataclass(slots=True, frozen=True)
class MemoryStats:
    """memory 状态统计。"""

    friend_messages: int
    dialogue_windows: int
    response_pairs: int
    live_messages: int
    relationship_memories: int = 0


@dataclass(slots=True, frozen=True)
class DbPerfRecord:
    """一次 LanceDB 操作耗时记录。"""

    ts_ms: int
    op: str
    table: str
    latency_ms: float
    rows: int = 0
    status: str = "ok"
    detail: str = ""


@dataclass(slots=True)
class DbPerfStats:
    """按 op/table 聚合后的 LanceDB 性能统计。"""

    count: int = 0
    error_count: int = 0
    total_latency_ms: float = 0.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    rows: int = 0
    last_records: list[DbPerfRecord] = field(default_factory=list)

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.count if self.count else 0.0

    @property
    def error_rate(self) -> float:
        return self.error_count / self.count if self.count else 0.0


class MemoryStore:
    """LanceDB 存储门面。

    使用方式：
        store = MemoryStore(settings)
        await store.connect()
        store.ensure_tables()
        await store.upsert_friend_chunks(chunks, embeddings)
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._db: DBConnection | None = None
        self._write_lock = asyncio.Lock()
        self._db_perf: deque[DbPerfRecord] = deque(maxlen=_DB_PERF_LAST_LIMIT)

    # ------------------------------------------------------------------
    # 连接 / 建表
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """同步打开数据库目录（lancedb.connect 本身是同步的，但封装为 async 方便统一接口）。"""
        if self._db is not None:
            return
        path = Path(self.settings.lance_db_path)
        path.mkdir(parents=True, exist_ok=True)
        # lancedb.connect 是同步的，直接调用即可
        self._db = lancedb.connect(str(path))

    def ensure_tables(self) -> None:
        """初次启动建表。已存在则跳过。

        设计上 schema 只增不改：新增可空字段时，旧库读到 null 等价于"未填"，
        不会破坏向量召回；如果哪天真要做破坏性 schema 变更，再单独写迁移命令。
        """
        db = self._require_db()
        dim = self.settings.embedding_dim
        existing = set(_list_table_names(db))
        if TABLE_FRIEND_MESSAGES not in existing:
            db.create_table(TABLE_FRIEND_MESSAGES, schema=friend_messages_schema(dim))
        if TABLE_DIALOGUE_WINDOWS not in existing:
            db.create_table(TABLE_DIALOGUE_WINDOWS, schema=dialogue_windows_schema(dim))
        if TABLE_RESPONSE_PAIRS not in existing:
            db.create_table(TABLE_RESPONSE_PAIRS, schema=response_pairs_schema(dim))
        if TABLE_LIVE_MESSAGES not in existing:
            db.create_table(TABLE_LIVE_MESSAGES, schema=live_messages_schema(dim))
        if TABLE_RELATIONSHIP_MEMORIES not in existing:
            db.create_table(
                TABLE_RELATIONSHIP_MEMORIES,
                schema=relationship_memories_schema(dim),
            )

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    async def upsert_friend_chunks(
        self,
        chunks: Iterable[FriendMessageChunk],
        embeddings: dict[str, list[float]],
    ) -> int:
        """写入 friend_messages 表。

        - embeddings: dict[chunk_id, vector]，必须为每个 chunk 提供向量。
        - 重复 id 走 merge_insert（覆盖更新）。
        """
        rows = self._build_friend_rows(chunks, embeddings)
        if not rows:
            return 0
        return await self._upsert_rows(TABLE_FRIEND_MESSAGES, rows, friend_messages_schema(self.settings.embedding_dim))

    async def upsert_window_chunks(
        self,
        chunks: Iterable[DialogueWindowChunk],
        embeddings: dict[str, list[float]],
    ) -> int:
        rows = self._build_window_rows(chunks, embeddings)
        if not rows:
            return 0
        return await self._upsert_rows(TABLE_DIALOGUE_WINDOWS, rows, dialogue_windows_schema(self.settings.embedding_dim))

    async def upsert_response_pair_chunks(
        self,
        chunks: Iterable[ResponsePairChunk],
        embeddings: dict[str, list[float]],
    ) -> int:
        rows = self._build_response_pair_rows(chunks, embeddings)
        if not rows:
            return 0
        return await self._upsert_rows(
            TABLE_RESPONSE_PAIRS,
            rows,
            response_pairs_schema(self.settings.embedding_dim),
        )

    async def append_live_messages(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        # 复制行并补默认字段，避免修改调用方传入的对象
        ts = now_ms()
        prepared: list[dict[str, Any]] = []
        for r in rows:
            row = dict(r)
            row.setdefault("source", "live")
            row.setdefault("confirmed", True)
            row.setdefault("trust_level", 0.35)
            row.setdefault("deleted", False)
            row.setdefault("created_at_ms", ts)
            row.setdefault("attachments", [])
            prepared.append(row)
        return await self._upsert_rows(
            TABLE_LIVE_MESSAGES,
            prepared,
            live_messages_schema(self.settings.embedding_dim),
        )

    async def upsert_relationship_memories(self, rows: list[dict[str, Any]]) -> int:
        """写入新关系长期记忆。"""
        if not rows:
            return 0
        ts = now_ms()
        prepared: list[dict[str, Any]] = []
        for r in rows:
            row = dict(r)
            row.setdefault("kind", "note")
            row.setdefault("importance", 1)
            row.setdefault("source", "chat")
            row.setdefault("conversation_id", "")
            row.setdefault("created_at_ms", ts)
            row.setdefault("updated_at_ms", ts)
            row.setdefault("deleted", False)
            prepared.append(row)
        return await self._upsert_rows(
            TABLE_RELATIONSHIP_MEMORIES,
            prepared,
            relationship_memories_schema(self.settings.embedding_dim),
        )

    async def soft_delete(self, table: str, row_id: str) -> bool:
        """软删除一行：把 deleted 置 true。

        使用 LanceDB 的 update 表达式（已对 row_id 做转义）。
        """
        async with self._write_lock:
            start = time.perf_counter()
            tbl = self._table(table)
            where = f"id = {_quote_lance(row_id)}"
            try:
                arrow_tbl = tbl.search().where(where).limit(1).to_arrow()
                if arrow_tbl.num_rows == 0:
                    self._record_db_perf(
                        "soft_delete",
                        table,
                        start,
                        rows=0,
                        detail="not_found",
                    )
                    return False
                tbl.update(where=where, values={"deleted": True})
                self._record_db_perf("soft_delete", table, start, rows=1)
                return True
            except Exception as e:
                self._record_db_perf(
                    "soft_delete",
                    table,
                    start,
                    status="error",
                    detail=type(e).__name__,
                )
                raise

    async def list_unlabeled_friend_chunks(self, limit: int = 1000) -> list[dict[str, Any]]:
        """列出 friend_messages 中还没打标的 chunk（mood 为空或缺失）。

        用于离线增量打标：上层只对返回的这些跑 LLM。
        """
        tbl = self._table(TABLE_FRIEND_MESSAGES)
        start = time.perf_counter()
        # FTS / null 过滤：LanceDB 表达式不支持 NULL/IS NULL，需要用空字符串
        try:
            arrow_tbl = (
                tbl.search()
                .where("deleted = false AND (mood IS NULL OR mood = '')")
                .limit(limit)
                .to_arrow()
            )
            rows = cast(list[dict[str, Any]], arrow_tbl.to_pylist())
            self._record_db_perf(
                "list_unlabeled",
                TABLE_FRIEND_MESSAGES,
                start,
                rows=len(rows),
                detail=f"limit={limit}",
            )
            return rows
        except Exception as e:
            self._record_db_perf(
                "list_unlabeled",
                TABLE_FRIEND_MESSAGES,
                start,
                status="error",
                detail=type(e).__name__,
            )
            raise

    async def update_labels(
        self,
        table: str,
        updates: list[dict[str, Any]],
    ) -> int:
        """批量回填标签字段。

        updates 元素：{"id": ..., "mood": ..., "topic": ..., "importance": ...}
        - 用 LanceDB merge_insert 批量按 id 更新，只改标签列
        - 不插入不存在的 id，避免打标阶段写出孤儿记录
        """
        prepared: list[dict[str, Any]] = []
        for row in updates:
            row_id = row.get("id")
            if not isinstance(row_id, str) or not row_id:
                continue
            prepared.append(
                {
                    "id": row_id,
                    "mood": str(row.get("mood") or ""),
                    "topic": str(row.get("topic") or ""),
                    "importance": int(row.get("importance") or 0),
                }
            )
        if not prepared:
            return 0

        label_schema = pa.schema(
            [
                pa.field("id", pa.string(), nullable=False),
                pa.field("mood", pa.string()),
                pa.field("topic", pa.string()),
                pa.field("importance", pa.int8()),
            ]
        )
        async with self._write_lock:
            start = time.perf_counter()
            tbl = self._table(table)
            total = 0
            try:
                batch_size = self._upsert_batch_size()
                for offset in range(0, len(prepared), batch_size):
                    chunk = prepared[offset : offset + batch_size]
                    arrow_tbl = pa.Table.from_pylist(chunk, schema=label_schema)
                    tbl.merge_insert("id").when_matched_update_all().execute(
                        arrow_tbl
                    )
                    total += len(chunk)
                self._record_db_perf("update_labels", table, start, rows=total)
                return total
            except Exception as e:
                self._record_db_perf(
                    "update_labels",
                    table,
                    start,
                    rows=total,
                    status="error",
                    detail=type(e).__name__,
                )
                raise

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------

    async def search_friend(
        self,
        vector: list[float],
        top_k: int,
        *,
        extra_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._vector_search(TABLE_FRIEND_MESSAGES, vector, top_k, extra_filter)

    async def search_windows(
        self,
        vector: list[float],
        top_k: int,
        *,
        extra_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._vector_search(TABLE_DIALOGUE_WINDOWS, vector, top_k, extra_filter)

    async def search_response_pairs(
        self,
        vector: list[float],
        top_k: int,
        *,
        extra_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._vector_search(TABLE_RESPONSE_PAIRS, vector, top_k, extra_filter)

    async def search_relationship_memories(
        self,
        vector: list[float],
        top_k: int,
    ) -> list[dict[str, Any]]:
        return self._vector_search(TABLE_RELATIONSHIP_MEMORIES, vector, top_k, None)

    async def search_live(
        self,
        vector: list[float],
        top_k: int,
        *,
        extra_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """live_messages 表的向量召回。

        默认通过 extra_filter 排除跨会话的 ai_generated（让 AI 自我历史不污染语义检索）。
        retriever 会根据 settings.ai_generated_long_term_enabled 决定具体 filter。
        """
        return self._vector_search(TABLE_LIVE_MESSAGES, vector, top_k, extra_filter)

    async def cleanup_ai_generated(
        self,
        *,
        older_than_days: int = 0,
        conversation_id: str | None = None,
        dry_run: bool = False,
    ) -> int:
        """软删除 live_messages 中 source=ai_generated 的行。

        - older_than_days=0 时清理全部 ai_generated（不限时间）
        - 指定 conversation_id 时只清这个会话
        - dry_run=True 时只返回会被清理的行数，不写库
        """
        tbl = self._table(TABLE_LIVE_MESSAGES)
        where = "source = 'ai_generated' AND deleted = false"
        if conversation_id:
            cid = _quote_lance(conversation_id)
            where = f"{where} AND conversation_id = {cid}"
        if older_than_days > 0:
            cutoff = now_ms() - older_than_days * 86_400_000
            where = f"{where} AND created_at_ms < {cutoff}"
        try:
            arrow_tbl = tbl.search().where(where).limit(10_000).to_arrow()
            rows = cast(list[dict[str, Any]], arrow_tbl.to_pylist())
        except Exception as e:
            logger.warning("cleanup_ai_generated 查询失败：%s", type(e).__name__)
            return 0
        count = len(rows)
        if dry_run or count == 0:
            return count
        ids = [str(r.get("id")) for r in rows if r.get("id")]
        if not ids:
            return 0
        try:
            id_list = ", ".join(_quote_lance(i) for i in ids)
            tbl.update(where=f"id IN ({id_list})", values={"deleted": True})
        except Exception as e:
            logger.warning("cleanup_ai_generated 软删除失败：%s", type(e).__name__)
            return 0
        return count

    async def recent_live(
        self,
        conversation_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        tbl = self._table(TABLE_LIVE_MESSAGES)
        start = time.perf_counter()
        # LanceDB 不支持非向量检索时的 ORDER BY，因此先查全部再排序。
        # live_messages 通常很小，这样可以接受。
        # 字符串值用 _quote_lance 转义，避免单引号注入。
        where = f"conversation_id = {_quote_lance(conversation_id)} AND deleted = false"
        try:
            arrow_tbl = tbl.search().where(where).limit(limit * 4).to_arrow()
            rows = cast(list[dict[str, Any]], arrow_tbl.to_pylist())
            rows.sort(key=lambda r: r.get("created_at_ms") or 0, reverse=True)
            out = rows[:limit]
            self._record_db_perf(
                "recent_live",
                TABLE_LIVE_MESSAGES,
                start,
                rows=len(out),
                detail=f"limit={limit}",
            )
            return out
        except Exception as e:
            self._record_db_perf(
                "recent_live",
                TABLE_LIVE_MESSAGES,
                start,
                status="error",
                detail=type(e).__name__,
            )
            raise

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------

    async def stats(self) -> MemoryStats:
        db = self._require_db()
        start = time.perf_counter()
        try:
            stats = MemoryStats(
                friend_messages=self._safe_count(db, TABLE_FRIEND_MESSAGES),
                dialogue_windows=self._safe_count(db, TABLE_DIALOGUE_WINDOWS),
                response_pairs=self._safe_count(db, TABLE_RESPONSE_PAIRS),
                live_messages=self._safe_count(db, TABLE_LIVE_MESSAGES),
                relationship_memories=self._safe_count(db, TABLE_RELATIONSHIP_MEMORIES),
            )
            self._record_db_perf(
                "stats",
                "*",
                start,
                rows=(
                    stats.friend_messages
                    + stats.dialogue_windows
                    + stats.response_pairs
                    + stats.live_messages
                    + stats.relationship_memories
                ),
            )
            return stats
        except Exception as e:
            self._record_db_perf(
                "stats",
                "*",
                start,
                status="error",
                detail=type(e).__name__,
            )
            raise

    def db_perf_snapshot(self) -> dict[str, Any]:
        records = list(self._db_perf)
        grouped: dict[str, list[DbPerfRecord]] = {}
        for record in records:
            grouped.setdefault(f"{record.op}:{record.table}", []).append(record)
        return {
            "recent": [_db_record_to_dict(r) for r in records[-30:]],
            "by_operation": {
                key: _db_stats_to_dict(_db_stats(value))
                for key, value in sorted(grouped.items())
            },
            "slowest": [
                _db_record_to_dict(r)
                for r in sorted(records, key=lambda x: x.latency_ms, reverse=True)[:10]
            ],
        }

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _require_db(self) -> DBConnection:
        if self._db is None:
            raise StoreError("MemoryStore 尚未 connect()，无法访问数据库")
        return self._db

    def _table(self, name: str) -> Table:
        return self._require_db().open_table(name)

    def _safe_count(self, db: DBConnection, name: str) -> int:
        """统计行数。

        表不存在视为 0；其它异常记录 warning 后返回 0，避免静默掩盖数据库损坏。
        """
        try:
            return int(db.open_table(name).count_rows())
        except FileNotFoundError:
            return 0
        except Exception as e:
            logger.warning("无法统计表 %s 的行数：%s", name, type(e).__name__)
            return 0

    def _vector_search(
        self,
        table: str,
        vector: list[float],
        top_k: int,
        extra_filter: str | None,
    ) -> list[dict[str, Any]]:
        tbl = self._table(table)
        start = time.perf_counter()
        try:
            q = tbl.search(np.asarray(vector, dtype=np.float32)).limit(top_k)
            cond = "deleted = false"
            if extra_filter:
                cond = f"({cond}) AND ({extra_filter})"
            q = q.where(cond)
            arrow_tbl = q.to_arrow()
            rows = cast(list[dict[str, Any]], arrow_tbl.to_pylist())
            self._record_db_perf(
                "vector_search",
                table,
                start,
                rows=len(rows),
                detail=f"top_k={top_k}",
            )
            return rows
        except Exception as e:
            self._record_db_perf(
                "vector_search",
                table,
                start,
                status="error",
                detail=type(e).__name__,
            )
            raise

    async def _upsert_rows(
        self,
        table: str,
        rows: list[dict[str, Any]],
        schema: pa.Schema,
    ) -> int:
        if not rows:
            return 0
        async with self._write_lock:
            start = time.perf_counter()
            tbl = self._table(table)
            # 分批写入，避免 1w 条 × 4096 维向量一次性进内存 / spill
            total = 0
            try:
                batch_size = self._upsert_batch_size()
                for offset in range(0, len(rows), batch_size):
                    chunk = rows[offset : offset + batch_size]
                    arrow_tbl = pa.Table.from_pylist(chunk, schema=schema)
                    tbl.merge_insert("id").when_matched_update_all().when_not_matched_insert_all().execute(arrow_tbl)
                    total += len(chunk)
                self._record_db_perf("upsert", table, start, rows=total)
                return total
            except Exception as e:
                self._record_db_perf(
                    "upsert",
                    table,
                    start,
                    rows=total,
                    status="error",
                    detail=type(e).__name__,
                )
                raise

    def _upsert_batch_size(self) -> int:
        return max(1, int(self.settings.lance_upsert_batch_size or _UPSERT_BATCH_SIZE))

    def _record_db_perf(
        self,
        op: str,
        table: str,
        start: float,
        *,
        rows: int = 0,
        status: str = "ok",
        detail: str = "",
    ) -> None:
        self._db_perf.append(
            DbPerfRecord(
                ts_ms=int(time.time() * 1000),
                op=op,
                table=table,
                latency_ms=round((time.perf_counter() - start) * 1000, 2),
                rows=rows,
                status=status,
                detail=detail,
            )
        )

    def _build_friend_rows(
        self,
        chunks: Iterable[FriendMessageChunk],
        embeddings: dict[str, list[float]],
    ) -> list[dict[str, Any]]:
        ts = now_ms()
        rows: list[dict[str, Any]] = []
        for c in chunks:
            vec = embeddings.get(c.chunk_id)
            if vec is None:
                raise StoreError(f"chunk {c.chunk_id} 缺少向量")
            self._check_dim(vec)
            rows.append(
                {
                    "id": c.chunk_id,
                    "vector": vec,
                    "text": c.text,
                    "dialogue_snippet": c.dialogue_snippet,
                    "context_before": c.context_before,
                    "context_after": c.context_after,
                    "message_id": c.message_id,
                    "session_id": c.session_id,
                    "seq": c.seq,
                    "timestamp_ms": c.timestamp_ms,
                    "source": c.source,
                    "trust_level": c.trust_level,
                    "warmth": c.warmth,
                    "tags": c.tags,
                    "deleted": False,
                    "created_at_ms": ts,
                    # 标签字段默认空，由 labeler 异步填充
                    "mood": "",
                    "topic": "",
                    "importance": 1,
                }
            )
        return rows

    def _build_window_rows(
        self,
        chunks: Iterable[DialogueWindowChunk],
        embeddings: dict[str, list[float]],
    ) -> list[dict[str, Any]]:
        ts = now_ms()
        rows: list[dict[str, Any]] = []
        for c in chunks:
            vec = embeddings.get(c.chunk_id)
            if vec is None:
                raise StoreError(f"chunk {c.chunk_id} 缺少向量")
            self._check_dim(vec)
            rows.append(
                {
                    "id": c.chunk_id,
                    "vector": vec,
                    "text": c.text,
                    "summary": c.summary,
                    "session_id": c.session_id,
                    "start_seq": c.start_seq,
                    "end_seq": c.end_seq,
                    "start_time_ms": c.start_time_ms,
                    "end_time_ms": c.end_time_ms,
                    "message_count": c.message_count,
                    "has_media": c.has_media,
                    "source": c.source,
                    "trust_level": c.trust_level,
                    "tags": c.tags,
                    "deleted": False,
                    "created_at_ms": ts,
                }
            )
        return rows

    def _build_response_pair_rows(
        self,
        chunks: Iterable[ResponsePairChunk],
        embeddings: dict[str, list[float]],
    ) -> list[dict[str, Any]]:
        ts = now_ms()
        rows: list[dict[str, Any]] = []
        for c in chunks:
            vec = embeddings.get(c.chunk_id)
            if vec is None:
                raise StoreError(f"response pair {c.chunk_id} 缺少向量")
            self._check_dim(vec)
            rows.append(
                {
                    "id": c.chunk_id,
                    "vector": vec,
                    "text": c.user_text,
                    "friend_reply": c.friend_reply,
                    "dialogue_snippet": c.dialogue_snippet,
                    "user_message_ids": c.user_message_ids,
                    "friend_message_ids": c.friend_message_ids,
                    "session_id": c.session_id,
                    "start_seq": c.start_seq,
                    "end_seq": c.end_seq,
                    "start_time_ms": c.start_time_ms,
                    "end_time_ms": c.end_time_ms,
                    "source": c.source,
                    "trust_level": c.trust_level,
                    "warmth": c.warmth,
                    "tags": c.tags,
                    "deleted": False,
                    "created_at_ms": ts,
                }
            )
        return rows

    def _check_dim(self, vec: list[float]) -> None:
        if len(vec) != self.settings.embedding_dim:
            raise StoreError(
                f"向量维度不匹配：期望 {self.settings.embedding_dim}，实际 {len(vec)}"
            )


def _list_table_names(db: DBConnection) -> list[str]:
    """兼容不同 LanceDB 版本返回的表名列表。

    - 旧版 db.table_names() 直接返回 list[str]
    - 新版 db.list_tables() 返回 ListTablesResponse 对象，需取 .tables
    """
    result = db.list_tables()
    if isinstance(result, list):
        return result
    tables = getattr(result, "tables", None)
    if tables is None:
        return []
    return list(tables)


def _db_record_to_dict(record: DbPerfRecord) -> dict[str, Any]:
    return {
        "ts_ms": record.ts_ms,
        "op": record.op,
        "table": record.table,
        "latency_ms": record.latency_ms,
        "rows": record.rows,
        "status": record.status,
        "detail": record.detail,
    }


def _db_stats(records: list[DbPerfRecord]) -> DbPerfStats:
    count = len(records)
    if count == 0:
        return DbPerfStats()
    latencies = sorted(r.latency_ms for r in records)
    return DbPerfStats(
        count=count,
        error_count=sum(1 for r in records if r.status == "error"),
        total_latency_ms=round(sum(latencies), 2),
        p50_latency_ms=latencies[count // 2],
        p95_latency_ms=latencies[min(count - 1, int(count * 0.95))],
        max_latency_ms=max(latencies),
        rows=sum(r.rows for r in records),
        last_records=records[-10:],
    )


def _db_stats_to_dict(stats: DbPerfStats) -> dict[str, Any]:
    return {
        "count": stats.count,
        "error_count": stats.error_count,
        "error_rate": round(stats.error_rate, 4),
        "avg_latency_ms": round(stats.avg_latency_ms, 2),
        "p50_latency_ms": round(stats.p50_latency_ms, 2),
        "p95_latency_ms": round(stats.p95_latency_ms, 2),
        "max_latency_ms": round(stats.max_latency_ms, 2),
        "rows": stats.rows,
        "last": [_db_record_to_dict(r) for r in stats.last_records],
    }


def _quote_lance(value: str) -> str:
    """转义 LanceDB where 表达式中的字符串字面量，防止 SQL 注入。

    LanceDB 的 where 是 SQL-like 表达式，用单引号包裹字符串，内部单引号需要双写。
    """
    escaped = value.replace("'", "''")
    return f"'{escaped}'"
