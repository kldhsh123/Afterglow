"""异步回写队列（批量延迟模式）。

聊天完成后把 (user_message, assistant_message) 加到内存缓存，**不立刻向量化**。
- 当某个 conversation 累积 >= settings.writeback_batch_turns 轮时，触发该会话的 flush
- 后台 ticker 每 writeback_flush_interval_seconds 秒巡检，把不活跃的会话强制 flush
- stop(drain=True) 时 flush 所有未持久化轮次（服务退出不丢数据）

好处：每 N 轮才调一次 embedding API（而非每轮 2 次），按 single 模式可省 ~2N 倍 RTT。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

from xuwen.config import Settings
from xuwen.core.errors import EmbeddingError
from xuwen.core.time import now_ms
from xuwen.ingestion.embedder import EmbeddingClient
from xuwen.memory.store import MemoryStore

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class WritebackTurn:
    """一轮对话的回写请求。"""

    conversation_id: str
    user_text: str
    assistant_text: str
    # 用户消息附带的图片 SHA-256 列表（已在 chat 路由内 image_store.save 过）
    user_image_shas: list[str] = field(default_factory=list)


@dataclass(slots=True)
class WritebackStats:
    """运行时指标。"""

    enqueued: int = 0
    written: int = 0
    flushed_batches: int = 0
    dropped: int = 0
    failed: int = 0
    paused: bool = False
    pending_turns: int = 0


@dataclass(slots=True)
class _ConversationBuffer:
    """单个 conversation_id 的待 flush 缓冲。"""

    turns: list[WritebackTurn] = field(default_factory=list)
    last_enqueue_ts: float = 0.0


class WritebackQueue:
    """批量延迟回写队列。

    使用方式：
        wb = WritebackQueue(settings, store, embedder)
        await wb.start()
        await wb.enqueue_turn(turn)
        ...
        await wb.stop(drain=True)
    """

    def __init__(
        self,
        settings: Settings,
        store: MemoryStore,
        embedder: EmbeddingClient | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.embedder = embedder
        self._pending: dict[str, _ConversationBuffer] = {}
        self._lock = asyncio.Lock()
        self._ticker_task: asyncio.Task[None] | None = None
        self._flush_tasks: set[asyncio.Task[None]] = set()
        self._stopping = asyncio.Event()
        self.stats = WritebackStats(paused=not settings.writeback_enabled)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._ticker_task is not None:
            return
        self._stopping.clear()
        self._ticker_task = asyncio.create_task(self._ticker_loop(), name="xuwen-wb-ticker")

    async def stop(self, *, drain: bool = True) -> None:
        if self._ticker_task is None:
            return
        self._stopping.set()
        # 取消 ticker
        self._ticker_task.cancel()
        try:
            await self._ticker_task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("writeback ticker 退出异常：%s", type(e).__name__)
        self._ticker_task = None

        # 等待已经触发的异步 flush 完成
        if self._flush_tasks:
            await asyncio.gather(*self._flush_tasks, return_exceptions=True)

        if drain:
            await self.flush_all()

    # ------------------------------------------------------------------
    # 公开
    # ------------------------------------------------------------------

    def pause(self) -> None:
        self.stats.paused = True

    def resume(self) -> None:
        self.stats.paused = False

    async def enqueue_turn(self, turn: WritebackTurn) -> bool:
        """把一轮对话加入待 flush 缓冲。

        - 全局开关或运行时 pause 时直接丢弃，不报错。
        - 缓冲满 batch_turns 时立刻触发该会话的 flush。
        """
        if not self.settings.writeback_enabled or self.stats.paused:
            self.stats.dropped += 1
            return False

        # 总缓冲规模兜底（防内存爆炸）
        total = sum(len(b.turns) for b in self._pending.values())
        if total >= self.settings.writeback_queue_size:
            self.stats.dropped += 1
            logger.warning(
                "writeback 总缓冲超限（%d 轮），丢弃一轮", total
            )
            return False

        async with self._lock:
            buf = self._pending.setdefault(turn.conversation_id, _ConversationBuffer())
            buf.turns.append(turn)
            buf.last_enqueue_ts = time.monotonic()
            self.stats.enqueued += 1
            self.stats.pending_turns = sum(len(b.turns) for b in self._pending.values())
            should_flush = len(buf.turns) >= self.settings.writeback_batch_turns

        if should_flush:
            # 异步 flush，不阻塞调用方；保持引用避免 GC 提前回收
            task = asyncio.create_task(self._flush_conversation(turn.conversation_id))
            self._flush_tasks.add(task)
            task.add_done_callback(self._flush_tasks.discard)
        return True

    async def flush_all(self) -> None:
        """强制 flush 所有 pending。"""
        async with self._lock:
            conv_ids = list(self._pending.keys())
        for cid in conv_ids:
            await self._flush_conversation(cid)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    async def _ticker_loop(self) -> None:
        """周期性巡检，对超过 flush_interval 未活动的会话强制 flush。"""
        interval = max(1.0, self.settings.writeback_flush_interval_seconds / 4)
        try:
            while not self._stopping.is_set():
                await asyncio.sleep(interval)
                threshold = self.settings.writeback_flush_interval_seconds
                now = time.monotonic()
                async with self._lock:
                    candidates = [
                        cid
                        for cid, buf in self._pending.items()
                        if buf.turns and (now - buf.last_enqueue_ts) >= threshold
                    ]
                for cid in candidates:
                    await self._flush_conversation(cid)
        except asyncio.CancelledError:
            raise

    async def _flush_conversation(self, conversation_id: str) -> None:
        """把某个 conversation 的所有 pending 轮次批量持久化。"""
        async with self._lock:
            buf = self._pending.get(conversation_id)
            if buf is None or not buf.turns:
                return
            turns = buf.turns
            buf.turns = []
            self.stats.pending_turns = sum(len(b.turns) for b in self._pending.values())

        try:
            written = await self._persist_batch(conversation_id, turns)
            self.stats.flushed_batches += 1
            self.stats.written += written
        except Exception as e:
            self.stats.failed += len(turns)
            logger.warning(
                "writeback flush 失败（%s）：%s，丢弃 %d 轮",
                conversation_id,
                type(e).__name__,
                len(turns),
            )

    async def _persist_batch(
        self,
        conversation_id: str,
        turns: list[WritebackTurn],
    ) -> int:
        """批量向量化（如开启）+ 写库。

        分层策略：
        - 用户输入：source=user_new，trust=0.65（用户近况事实，不参与风格蒸馏）
        - AI 回复：source=ai_generated，trust=0.15（仅用于连续性检索）
        - 主动话题等无真实 user_text 的场景：只写 ai_generated 行
        """
        dim = self.settings.embedding_dim
        zero_vec = [0.0] * dim

        # 收集要写入的 (turn, text, role, source, trust, attachments)
        items: list[tuple[WritebackTurn, str, str, str, float, list[str]]] = []
        for t in turns:
            user_text = t.user_text.strip()
            if user_text and user_text not in _PROACTIVE_USER_MARKERS:
                items.append((t, user_text, "user", "user_new", 0.65, list(t.user_image_shas)))
            assistant_text = t.assistant_text.strip()
            if assistant_text:
                items.append((t, assistant_text, "assistant", "ai_generated", 0.15, []))

        if not items:
            return 0

        # 一次性向量化所有要写入的文本
        texts = [item[1] for item in items]
        vectors: list[list[float]] = [zero_vec] * len(items)
        if self.settings.writeback_vectorize and self.embedder is not None and texts:
            try:
                vectors = await self.embedder.embed_texts(texts)
            except EmbeddingError as e:
                logger.info(
                    "回写批量 embedding 失败，使用零向量兜底：%s",
                    type(e).__name__,
                )
                vectors = [zero_vec] * len(items)

        ts_base = now_ms()
        rows: list[dict[str, object]] = []
        for i, (_t, text, role, source, trust, attachments) in enumerate(items):
            prefix = "u" if role == "user" else "a"
            rows.append(
                {
                    "id": f"live-{prefix}-{uuid.uuid4().hex[:16]}",
                    "vector": vectors[i],
                    "text": text,
                    "role": role,
                    "conversation_id": conversation_id,
                    "confirmed": True,
                    "deleted": False,
                    "created_at_ms": ts_base + i,
                    "source": source,
                    "trust_level": trust,
                    "attachments": attachments,
                }
            )

        await self.store.append_live_messages(rows)
        return len(rows)


# 主动话题等场景下 user_text 可能为占位符（如"AI 主动开启话题"）；
# 这些不是用户真说的话，不应写入 user_new。
_PROACTIVE_USER_MARKERS: frozenset[str] = frozenset(
    {
        "AI 主动开启话题",
        "（AI 主动开启话题）",
    }
)
