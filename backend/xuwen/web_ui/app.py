"""配置 WebUI 的 FastAPI 子应用工厂。

由主 app 通过 app.mount() 挂载。鉴权独立于主 app：
- 优先使用 config_ui_setup_token（首次配置时未配 XUWEN_API_KEY 也能用）
- 若未设 setup_token，则回退到 xuwen_api_key
- localhost_only=true 时额外检查请求来源 IP

子应用挂载后，会把 settings 直接注入子 app 的 state，便于路由读取。

静态资源：
- 构建后的向导前端在 xuwen/web_ui/static/，挂在子 app 根路径下
- 用户访问 /config/ 时返回 index.html（向导入口）
"""

from __future__ import annotations

import ipaddress
import logging
import os
import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp

from xuwen.api_i18n import request_language
from xuwen.chat_api.middleware import install_exception_handlers
from xuwen.cli_i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, cli_text
from xuwen.config import Settings
from xuwen.error_i18n import error_text
from xuwen.prompt_language import language_scope
from xuwen.web_ui.first_run import FirstRunStatus, check_first_run
from xuwen.web_ui.routes import router as config_router

logger = logging.getLogger(__name__)


def _startup_language(settings: Settings | None = None) -> str:
    if settings is not None:
        return settings.xuwen_language
    value = os.getenv("XUWEN_LANGUAGE", DEFAULT_LANGUAGE)
    return value if value in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


# 这些路径在配置 UI 内无需鉴权：探活 + 静态资源
_OPEN_PATHS: set[str] = {
    "/ping",
    "/",
    "/index.html",
    "/favicon.svg",
    "/favicon.ico",
}


def _is_localhost(host: str | None) -> bool:
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host in ("localhost",)
    return ip.is_loopback


class ConfigUiAuth(BaseHTTPMiddleware):
    """配置 UI 自己的鉴权。

    设计：
    - 仅本机访问开关：config_ui_localhost_only=true 时拒绝非 127.0.0.1 / ::1 请求
    - token 校验：优先 setup_token，回退 xuwen_api_key
    - /ping 不需要 token，方便前端探活
    """

    def __init__(self, app: ASGIApp, settings: Settings, setup_token: str) -> None:
        super().__init__(app)
        self.settings = settings
        self.setup_token = setup_token

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        raw_settings = getattr(request.app.state, "settings", None)
        settings = raw_settings if isinstance(raw_settings, Settings) else self.settings
        # EventSource cannot set Accept-Language to the manually selected UI locale.
        language = request_language(
            request.query_params.get("language") or request.headers.get("accept-language"),
            default=settings.xuwen_language,
        )
        with language_scope(language):
            return await self._dispatch(request, call_next)

    async def _dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # 读 app.state.settings：put_values 后会被替换，确保 xuwen_api_key 等改动立即生效。
        # starlette State.__getattr__ 返回 Any 导致 Pylance 推断不出 Settings 类型，显式校验。
        raw_settings = getattr(request.app.state, "settings", None)
        settings: Settings = raw_settings if isinstance(raw_settings, Settings) else self.settings
        if settings.config_ui_localhost_only:
            client = request.client
            host = client.host if client else None
            if not _is_localhost(host):
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": {
                            "code": "config_ui.localhost_only",
                            "message": error_text(
                                "The configuration UI only allows local access. Use an SSH tunnel or disable CONFIG_UI_LOCALHOST_ONLY in .env for remote access."
                            ),
                        }
                    },
                )

        # 路径处理：mount 后 path 仍带前缀，剥一下方便判断
        prefix = settings.config_ui_path_prefix.rstrip("/")
        raw_path = request.url.path
        path = raw_path[len(prefix) :] if raw_path.startswith(prefix) else raw_path
        if not path.startswith("/"):
            path = "/" + path
        # 静态资源：放行常见前端构建产物路径（assets/、*.js、*.css、*.svg、*.ico、*.png）
        if path.startswith("/assets/") or path.endswith(
            (".js", ".css", ".svg", ".ico", ".png", ".woff", ".woff2")
        ):
            return await call_next(request)
        if path in _OPEN_PATHS:
            return await call_next(request)

        provided = _extract_token(request)
        expected_tokens: list[str] = [self.setup_token]
        api_key = settings.xuwen_api_key
        if api_key is not None:
            expected_tokens.append(api_key.get_secret_value())

        if not provided or provided not in expected_tokens:
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "code": "config_ui.auth",
                        "message": error_text(
                            "Unauthorized. Check the access token printed in the backend startup console and enter it here."
                        ),
                    }
                },
            )
        return await call_next(request)


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    api_key = request.headers.get("x-api-key")
    if api_key:
        return api_key.strip()
    # 也允许通过 query string 传 token（SSE 场景前端无法设 header）
    qp = request.query_params.get("token")
    if qp:
        return qp.strip()
    return None


def create_config_app(
    settings: Settings,
    *,
    first_run: FirstRunStatus | None = None,
    base_url: str = "http://127.0.0.1:8000",
) -> FastAPI:
    """构造配置 UI 子应用。

    first_run：调用方（chat_api.app）可传入预先计算好的首次模式状态。
    传 None 时这里自己算一次，方便独立测试。
    base_url：用于启动 banner 显示的访问地址，独立模式时应传入实际监听地址。
    """
    if first_run is None:
        first_run = check_first_run(settings)

    # 决定 setup token
    explicit = settings.config_ui_setup_token
    if explicit is not None and explicit.get_secret_value():
        setup_token = explicit.get_secret_value()
        token_source = "env"
    else:
        setup_token = secrets.token_urlsafe(24)
        token_source = "generated"

    app = FastAPI(
        title="Afterglow 配置 UI",
        version="0.1.0",
        docs_url=None,  # 不暴露 swagger，避免误以为是主 API 文档
        redoc_url=None,
        openapi_url=None,
    )
    # 直接挂在子 app 的 state 上，避免依赖父 app lifespan 完成后的 .xuwen
    app.state.settings = settings
    install_exception_handlers(app)

    app.add_middleware(ConfigUiAuth, settings=settings, setup_token=setup_token)

    app.include_router(config_router)

    # 构建后的向导前端
    static_dir = Path(__file__).parent / "static"
    index_html = static_dir / "index.html"

    if index_html.exists():
        # assets/ 目录由 Vite 输出，放 hashed 后的 js/css
        assets_dir = static_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        # 根路径 → index.html，运行时把 build-time 的 /config/ 替换为实际前缀，
        # 这样改 CONFIG_UI_PATH_PREFIX 不需要重新 build 前端。
        _build_time_prefix = "/config"
        _runtime_prefix = settings.config_ui_path_prefix.rstrip("/") or "/config"

        @app.get("/", include_in_schema=False)
        async def _index() -> HTMLResponse:
            text = index_html.read_text(encoding="utf-8")
            if _runtime_prefix != _build_time_prefix:
                text = text.replace(
                    f"{_build_time_prefix}/",
                    f"{_runtime_prefix}/",
                )
            return HTMLResponse(text)

        # 站点图标
        @app.get("/favicon.svg", include_in_schema=False)
        @app.get("/favicon.ico", include_in_schema=False)
        async def _favicon() -> Response:
            for name in ("favicon.svg", "favicon.ico"):
                p = static_dir / name
                if p.exists():
                    return FileResponse(str(p))
            return Response(status_code=404)
    else:

        @app.get("/", include_in_schema=False)
        async def _index_missing() -> JSONResponse:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "config_ui.not_built",
                        "message": (
                            error_text(
                                "The configuration UI assets have not been built. Run pnpm install && pnpm build in backend/web_ui_src/."
                            )
                        ),
                    }
                },
            )

    _log_startup_banner(settings, setup_token, token_source, first_run, base_url)
    return app


def _log_startup_banner(
    settings: Settings,
    setup_token: str,
    token_source: str,
    first_run: FirstRunStatus,
    base_url: str,
) -> None:
    language = _startup_language(settings)
    full_url = base_url.rstrip("/") + settings.config_ui_path_prefix + "/"
    if first_run.is_first_run:
        logger.warning(
            cli_text(
                "startup.first_run_log",
                language,
                missing=first_run.describe(),
                url=full_url,
                token=setup_token,
            ),
        )
        print(
            f"\n========================================\n"
            f"  {cli_text('startup.first_run_title', language, missing=first_run.describe())}\n"
            f"  {cli_text('startup.first_run_enabled', language)}\n"
            f"\n"
            f"  {cli_text('startup.browser', language, url=full_url)}\n"
            f"  {cli_text('startup.token', language, source=token_source, token=setup_token)}\n"
            f"\n"
            f"  {cli_text('startup.paste', language)}\n"
            f"  {cli_text('startup.restart', language)}\n"
            f"========================================\n",
            flush=True,
        )
    else:
        logger.warning(
            cli_text("startup.enabled_log", language, url=full_url, token=setup_token),
        )
        print(
            f"\n========================================\n"
            f"  {cli_text('startup.enabled_title', language)}\n"
            f"  {cli_text('startup.browser', language, url=full_url)}\n"
            f"  {cli_text('startup.token', language, source=token_source, token=setup_token)}\n"
            f"  {cli_text('startup.paste', language)}\n"
            f"========================================\n",
            flush=True,
        )
