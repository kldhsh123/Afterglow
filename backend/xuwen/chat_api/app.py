"""FastAPI app factory + 生命周期管理。

设计：
- `AppState` 在 `chat_api/state.py` 中定义，避免循环引用
- 通过 `app.dependency_overrides[get_state]` 注入真实实例
- 关闭时 drain writeback 队列、关闭 httpx clients
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from xuwen import __version__
from xuwen.chat_api.llm_client import LLMClient
from xuwen.chat_api.middleware import install_exception_handlers, install_middleware
from xuwen.chat_api.responses_store import ResponsesStore
from xuwen.chat_api.routes import chat as chat_route
from xuwen.chat_api.routes import companion as companion_route
from xuwen.chat_api.routes import debug as debug_route
from xuwen.chat_api.routes import documents as documents_route
from xuwen.chat_api.routes import health as health_route
from xuwen.chat_api.routes import images as images_route
from xuwen.chat_api.routes import memory as memory_route
from xuwen.chat_api.routes import responses as responses_route
from xuwen.chat_api.routes import stickers as stickers_route
from xuwen.chat_api.state import AppState, get_state
from xuwen.chat_api.web_fetch import WebFetchClient
from xuwen.chat_api.web_search import WebSearchClient
from xuwen.companion.life import LifeStateManager
from xuwen.companion.relationship import RelationshipMemoryManager
from xuwen.config import Settings, get_settings
from xuwen.core.metrics import MetricsRecorder
from xuwen.core.update_check import UpdateChecker
from xuwen.ingestion.embedder import EmbeddingClient
from xuwen.memory.cross_reranker import CrossReranker
from xuwen.memory.reranker import QueryRewriter, SemanticReranker
from xuwen.memory.retriever import HybridRetriever
from xuwen.memory.store import MemoryStore
from xuwen.memory.writer import WritebackQueue


def create_app(settings: Settings | None = None) -> FastAPI:
    """构造 FastAPI app。

    可传入 settings 方便测试覆盖；默认从环境读取。
    """
    resolved_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store = MemoryStore(resolved_settings)
        await store.connect()
        store.ensure_tables()

        embedder = EmbeddingClient(resolved_settings)
        llm = LLMClient(resolved_settings)
        # life / response_policy / query_rewrite / rerank 都是 fail-open 设计：
        # 单次失败立刻退快路径（缓存 / 规则层 / 原 query / RRF 顺序），重试无意义只会放大延迟。
        # 主聊天 llm 保留默认 max_retries=3，因为它没有兜底。
        life_llm = LLMClient(
            resolved_settings,
            api_url=resolved_settings.resolved_life_api_url,
            api_key=resolved_settings.resolved_life_api_key.get_secret_value(),
            max_retries=1,
        )
        response_policy_llm = LLMClient(
            resolved_settings,
            api_url=resolved_settings.resolved_response_policy_api_url,
            api_key=resolved_settings.resolved_response_policy_api_key.get_secret_value(),
            max_retries=1,
        )
        schedule_extractor_llm = LLMClient(
            resolved_settings,
            api_url=resolved_settings.resolved_schedule_api_url,
            api_key=resolved_settings.resolved_schedule_api_key.get_secret_value(),
            max_retries=1,
        )
        extra_llms: list[LLMClient] = []
        query_rewriter = None
        if resolved_settings.query_rewrite_enabled:
            query_rewrite_llm = LLMClient(
                resolved_settings,
                api_url=resolved_settings.resolved_query_rewrite_api_url,
                api_key=resolved_settings.resolved_query_rewrite_api_key.get_secret_value(),
                timeout_seconds=resolved_settings.rerank_timeout_seconds,
                max_retries=1,
            )
            extra_llms.append(query_rewrite_llm)
            query_rewriter = QueryRewriter(resolved_settings, query_rewrite_llm)

        semantic_reranker = None
        if (
            resolved_settings.rerank_enabled
            and resolved_settings.rerank_mode != "never"
        ):
            rerank_llm = LLMClient(
                resolved_settings,
                api_url=resolved_settings.resolved_rerank_api_url,
                api_key=resolved_settings.resolved_rerank_api_key.get_secret_value(),
                timeout_seconds=resolved_settings.rerank_timeout_seconds,
                max_retries=1,
            )
            extra_llms.append(rerank_llm)
            semantic_reranker = SemanticReranker(resolved_settings, rerank_llm)

        cross_reranker = None
        if resolved_settings.cross_rerank_enabled:
            if (
                not resolved_settings.cross_rerank_api_url
                or not resolved_settings.cross_rerank_api_key.get_secret_value()
                or not resolved_settings.cross_rerank_model
            ):
                # 启用了但没配齐三件套：忽略 + 警告，主链路继续走 RRF + 可选 LLM 精排
                import logging as _logging

                _logging.getLogger(__name__).warning(
                    "CROSS_RERANK_ENABLED=true 但 URL/KEY/MODEL 未配齐，已忽略 cross-encoder 粗排"
                )
            else:
                cross_reranker = CrossReranker(resolved_settings)

        retriever = HybridRetriever(
            resolved_settings,
            store=store,
            embedder=embedder,
            query_rewriter=query_rewriter,
            reranker=semantic_reranker,
            cross_reranker=cross_reranker,
        )
        writeback = WritebackQueue(resolved_settings, store=store, embedder=embedder)
        await writeback.start()
        metrics = MetricsRecorder(capacity=resolved_settings.metrics_capacity)
        life = LifeStateManager(resolved_settings)
        relationship_memory = RelationshipMemoryManager(
            resolved_settings,
            store=store,
            embedder=embedder,
        )
        web_search = (
            WebSearchClient(resolved_settings)
            if resolved_settings.web_access_enabled
            else None
        )
        web_fetch = (
            WebFetchClient(resolved_settings)
            if resolved_settings.web_access_enabled and resolved_settings.web_fetch_enabled
            else None
        )
        update_checker = UpdateChecker(
            resolved_settings,
            current_version=__version__,
        )

        state = AppState(
            settings=resolved_settings,
            store=store,
            embedder=embedder,
            llm=llm,
            life_llm=life_llm,
            response_policy_llm=response_policy_llm,
            schedule_extractor_llm=schedule_extractor_llm,
            retriever=retriever,
            writeback=writeback,
            metrics=metrics,
            life=life,
            relationship_memory=relationship_memory,
            responses_store=ResponsesStore(
                capacity=resolved_settings.responses_store_capacity,
            ),
            update_checker=update_checker,
            web_search=web_search,
            web_fetch=web_fetch,
        )
        app.state.xuwen = state

        # dependency override：让各 route 通过 Depends(get_state) 拿到真实 state
        app.dependency_overrides[get_state] = lambda: state

        # 版本更新检查：启动时 fire-and-forget 跑一次，结果（已是最新版 / 发现
        # 新版本 / 失败 / 已禁用）会打印到 stdout。不阻塞 lifespan；不再周期重复，
        # 想再查由前端"立即检查"按钮（POST /info/check-update）触发。
        await update_checker.start()

        try:
            yield
        finally:
            await update_checker.stop()
            await writeback.stop(drain=True)
            # 等剩余的 life marker fire-and-forget task 完成，避免进程退出时丢失最后几次 marker
            pending = [t for t in state.pending_life_tasks if not t.done()]
            if pending:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*pending, return_exceptions=True),
                        timeout=5.0,
                    )
                except TimeoutError:
                    # 5s 内还没跑完的就放弃，避免阻塞 shutdown 太久
                    for t in pending:
                        if not t.done():
                            t.cancel()
            await embedder.aclose()
            await llm.aclose()
            await life_llm.aclose()
            await response_policy_llm.aclose()
            await schedule_extractor_llm.aclose()
            for extra_llm in extra_llms:
                await extra_llm.aclose()
            if cross_reranker is not None:
                await cross_reranker.aclose()
            if web_search is not None:
                await web_search.aclose()
            if web_fetch is not None:
                await web_fetch.aclose()

    app = FastAPI(
        title=resolved_settings.app_name,
        description=resolved_settings.app_slogan,
        version=__version__,
        lifespan=lifespan,
    )

    install_middleware(app, resolved_settings)
    install_exception_handlers(app)

    app.include_router(health_route.router)
    app.include_router(memory_route.router)
    app.include_router(chat_route.router)
    app.include_router(responses_route.router)
    app.include_router(images_route.router)
    app.include_router(documents_route.router)
    app.include_router(stickers_route.router)
    app.include_router(companion_route.router)
    if resolved_settings.debug_endpoints_enabled:
        app.include_router(debug_route.router)

    # 配置 WebUI（小白向导）：由 CONFIG_UI_ENABLED 开关挂载
    # 走独立的鉴权 token 和 localhost-only 中间件，不影响主 API。
    # 首次模式：检测到关键字段缺失时强制启用，让小白第一次也能进得去配置 UI。
    from xuwen.web_ui.first_run import check_first_run

    first_run = check_first_run(resolved_settings)
    should_enable_config_ui = resolved_settings.config_ui_enabled or first_run.is_first_run

    if should_enable_config_ui:
        from xuwen.web_ui import create_config_app

        config_app = create_config_app(
            resolved_settings,
            first_run=first_run,
        )
        app.mount(resolved_settings.config_ui_path_prefix, config_app)

    return app
