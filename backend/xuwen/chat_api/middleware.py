"""中间件：本地 API key 守卫 + CORS + 错误处理 + 请求 id。"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from xuwen.config import Settings
from xuwen.core.errors import XuwenError

logger = logging.getLogger(__name__)

# 这些路径无需鉴权：只保留不触发模型、不暴露配置/数据的存活检查。
_OPEN_PATHS: set[str] = {
    "/healthz",
}


class ApiKeyGuard(BaseHTTPMiddleware):
    """API key 守卫。

    默认情况下，除 /healthz 外的所有后端路由都需要鉴权。
    `API_AUTH_REQUIRED=false` 只建议纯本地开发/测试使用；只要配置了
    `XUWEN_API_KEY`，即使关闭强制鉴权也仍会校验 token。
    """

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        api_key = self.settings.xuwen_api_key
        path = request.url.path
        if path in _OPEN_PATHS:
            return await call_next(request)
        # 配置 UI 子应用有自己的独立鉴权（setup token + localhost-only），
        # 主鉴权放行其前缀下的所有请求，避免双重拦截。
        # 首次模式：即使 CONFIG_UI_ENABLED=false，关键字段缺失时也会自动挂载，所以这里同样放行。
        from xuwen.web_ui.first_run import check_first_run

        config_ui_active = (
            self.settings.config_ui_enabled
            or check_first_run(self.settings).is_first_run
        )
        if config_ui_active:
            prefix = self.settings.config_ui_path_prefix.rstrip("/")
            if path == prefix or path.startswith(prefix + "/"):
                return await call_next(request)
        if api_key is None:
            if not self.settings.api_auth_required:
                return await call_next(request)
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "xuwen.auth_config",
                        "message": "后端 API 鉴权已启用，但 XUWEN_API_KEY 未配置。请先在 .env 设置本地 API key。",
                    }
                },
            )

        expected = api_key.get_secret_value()
        provided = _extract_token(request)
        if provided != expected:
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "code": "xuwen.auth",
                        "message": "缺少或错误的 API key（请在 Header 中带 Authorization: Bearer <key>）",
                    }
                },
            )
        return await call_next(request)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """为每个请求分配 x-request-id，便于追踪日志。"""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = rid
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration = (time.perf_counter() - start) * 1000
            logger.exception("请求处理异常 path=%s rid=%s elapsed_ms=%.1f", request.url.path, rid, duration)
            raise
        duration = (time.perf_counter() - start) * 1000
        response.headers["x-request-id"] = rid
        logger.info(
            "请求完成 method=%s path=%s status=%d rid=%s elapsed_ms=%.1f",
            request.method,
            request.url.path,
            response.status_code,
            rid,
            duration,
        )
        return response


def install_middleware(app: FastAPI, settings: Settings) -> None:
    """挂载所有中间件。

    顺序很重要：
        - 最外层是 CORS（让浏览器先通过预检）
        - 中间是 RequestId（每个请求都有 id）
        - 最内层是 ApiKeyGuard（已经经过 CORS 处理）

    在 FastAPI 中 add_middleware 是栈式注册（最后注册的最先执行），
    所以这里的注册顺序是从内到外。
    """
    app.add_middleware(ApiKeyGuard, settings=settings)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["x-request-id"],
    )


def install_exception_handlers(app: FastAPI) -> None:
    """统一把 XuwenError 转成结构化错误响应。"""

    @app.exception_handler(XuwenError)
    async def _xuwen_error_handler(request: Request, exc: XuwenError) -> JSONResponse:
        rid = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "request_id": rid,
                }
            },
            headers={"x-request-id": rid} if rid else None,
        )


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    api_key = request.headers.get("x-api-key")
    if api_key:
        return api_key.strip()
    return None
