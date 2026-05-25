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
import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp

from xuwen.config import Settings
from xuwen.web_ui.first_run import FirstRunStatus, check_first_run
from xuwen.web_ui.routes import router as config_router

logger = logging.getLogger(__name__)

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
                            "message": "配置 UI 仅允许本机访问。要远程访问请走 SSH 隧道或在 .env 关闭 CONFIG_UI_LOCALHOST_ONLY。",
                        }
                    },
                )

        # 路径处理：mount 后 path 仍带前缀，剥一下方便判断
        prefix = settings.config_ui_path_prefix.rstrip("/")
        raw_path = request.url.path
        path = raw_path[len(prefix):] if raw_path.startswith(prefix) else raw_path
        if not path.startswith("/"):
            path = "/" + path
        # 静态资源：放行常见前端构建产物路径（assets/、*.js、*.css、*.svg、*.ico、*.png）
        if path.startswith("/assets/") or path.endswith((".js", ".css", ".svg", ".ico", ".png", ".woff", ".woff2")):
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
                        "message": "未授权，请检查并输入后端启动时打印到控制台的访问token。",
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

        # favicon
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
                            "配置 UI 静态资源未构建。请在 backend/web_ui_src/ 下运行 "
                            "pnpm install && pnpm build。"
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
    full_url = base_url.rstrip("/") + settings.config_ui_path_prefix + "/"
    if first_run.is_first_run:
        logger.warning(
            "首次配置模式：缺失 %s，已自动启用配置 UI。访问 %s（token：%s）",
            first_run.describe(),
            full_url,
            setup_token,
        )
        print(
            f"\n========================================\n"
            f"  检测到首次配置（缺少 {first_run.describe()}）\n"
            f"  已自动启用配置 UI（仅本次会话）\n"
            f"\n"
            f"  浏览器访问：{full_url}\n"
            f"  访问 token（{token_source}）：{setup_token}\n"
            f"\n"
            f"  把这串 token 粘到向导第 1 步的输入框即可。\n"
            f"  配置完成并重启后，此提示将消失。\n"
            f"========================================\n",
            flush=True,
        )
    else:
        logger.warning(
            "配置 UI 已启用。访问 %s（token：%s）",
            full_url,
            setup_token,
        )
        print(
            f"\n========================================\n"
            f"  Afterglow 配置 UI 已启用\n"
            f"  浏览器访问：{full_url}\n"
            f"  访问 token（{token_source}）：{setup_token}\n"
            f"  把这串 token 粘到向导第 1 步的输入框即可。\n"
            f"========================================\n",
            flush=True,
        )
