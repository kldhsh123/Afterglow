"""中间件：本地 API key 守卫 + CORS + 错误处理 + 请求 id。"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from xuwen.api_i18n import api_text, request_language
from xuwen.config import Settings
from xuwen.content_i18n import content_text
from xuwen.core.errors import XuwenError
from xuwen.error_i18n import error_text
from xuwen.prompt_language import language_scope

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
            self.settings.config_ui_enabled or check_first_run(self.settings).is_first_run
        )
        if config_ui_active:
            prefix = self.settings.config_ui_path_prefix.rstrip("/")
            if path == prefix or path.startswith(prefix + "/"):
                return await call_next(request)
        if api_key is None:
            if not self.settings.api_auth_required:
                return await call_next(request)
            language = request_language(
                request.headers.get("accept-language"), default=self.settings.xuwen_language
            )
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "xuwen.auth_config",
                        "message": api_text("auth.config_missing", language),
                    }
                },
            )

        expected = api_key.get_secret_value()
        provided = _extract_token(request)
        if provided != expected:
            language = request_language(
                request.headers.get("accept-language"), default=self.settings.xuwen_language
            )
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "code": "xuwen.auth",
                        "message": api_text("auth.invalid", language),
                    }
                },
            )
        return await call_next(request)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """为每个请求分配 x-request-id，便于追踪日志。"""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = rid
        start = time.perf_counter()
        try:
            with language_scope(
                request_language(
                    request.headers.get("accept-language"), default=self.settings.xuwen_language
                )
            ):
                response = await call_next(request)
        except Exception:
            duration = (time.perf_counter() - start) * 1000
            logger.exception(
                content_text("Request failed path=%s rid=%s elapsed_ms=%.1f"),
                request.url.path,
                rid,
                duration,
            )
            raise
        duration = (time.perf_counter() - start) * 1000
        response.headers["x-request-id"] = rid
        logger.info(
            content_text("Request complete method=%s path=%s status=%d rid=%s elapsed_ms=%.1f"),
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
    app.add_middleware(RequestIdMiddleware, settings=settings)
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

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = []
        for entry in exc.errors():
            item = dict(entry)
            kind = str(item.get("type", ""))
            if kind == "missing":
                item["msg"] = error_text("This field is required")
            elif kind == "json_invalid":
                item["msg"] = error_text("Invalid JSON")
            elif kind == "value_error" and isinstance(item.get("ctx"), dict):
                item["msg"] = error_text(
                    "Invalid value: {reason}", reason=item["ctx"].get("error", "")
                )
            else:
                item["msg"] = error_text("Invalid value: {reason}", reason=item.get("msg", ""))
            errors.append(item)
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})

    @app.exception_handler(StarletteHTTPException)
    async def _http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        if detail in ("Not Found", "Method Not Allowed", "Unauthorized", "Forbidden"):
            detail = error_text(detail)
        return JSONResponse(
            status_code=exc.status_code, content={"detail": detail}, headers=exc.headers
        )


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    api_key = request.headers.get("x-api-key")
    if api_key:
        return api_key.strip()
    return None
