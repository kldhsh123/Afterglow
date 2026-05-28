"""共享应用状态 + dependency injection 占位。

把 `AppState` 单独放在这里避免 routes 与 app.py 之间的循环引用。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from xuwen.chat_api.llm_client import LLMClient
from xuwen.chat_api.responses_store import ResponsesStore
from xuwen.chat_api.web_fetch import WebFetchClient
from xuwen.chat_api.web_search import WebSearchClient
from xuwen.companion.life import LifeStateManager
from xuwen.companion.relationship import RelationshipMemoryManager
from xuwen.config import Settings
from xuwen.core.metrics import MetricsRecorder
from xuwen.core.update_check import UpdateChecker
from xuwen.ingestion.embedder import EmbeddingClient
from xuwen.memory.retriever import HybridRetriever
from xuwen.memory.store import MemoryStore
from xuwen.memory.writer import WritebackQueue


@dataclass(slots=True)
class AppState:
    """FastAPI app 启动时构造，所有 route 通过 Depends(get_state) 拿到。"""

    settings: Settings
    store: MemoryStore
    embedder: EmbeddingClient
    llm: LLMClient
    life_llm: LLMClient
    response_policy_llm: LLMClient
    retriever: HybridRetriever
    writeback: WritebackQueue
    metrics: MetricsRecorder
    life: LifeStateManager
    relationship_memory: RelationshipMemoryManager
    responses_store: ResponsesStore
    update_checker: UpdateChecker
    # 串行化所有 life.apply_marker_patch 的 fire-and-forget task，避免并发写同一份 life state 文件。
    # asyncio.Lock 必须在 event loop 启动后创建，所以 lifespan 里再赋值；field(default=None) 占位。
    life_apply_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # 追踪 fire-and-forget 的 marker apply task。
    # 必须是强引用 set —— asyncio.create_task 的返回值如果只被 weakref 持有，event loop
    # 不会保证 task 一定跑完，GC 可能在中途丢掉它。done_callback 在任务完成时自动从集合移除。
    pending_life_tasks: set[asyncio.Task[None]] = field(default_factory=set)
    web_search: WebSearchClient | None = None
    web_fetch: WebFetchClient | None = None


def get_state() -> AppState:
    """占位 dependency。

    实际值由 `create_app()` 在 lifespan 中通过 `app.dependency_overrides` 替换。
    在 override 生效前调用会抛错。
    """
    raise RuntimeError(
        "AppState 尚未注入：请确认 chat_api.app:create_app() 已正确启动"
    )
