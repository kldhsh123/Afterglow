"""独立配置 UI 启动入口。

用法：
    uv run python -m xuwen.web_ui
    uv run python -m xuwen.web_ui --port 9000 --host 0.0.0.0

适用场景：
- 已经配过一次，但项目升级后想改配置（加新字段、换模型等）
- 不想跑全套主 lifespan（LanceDB connect / 更新检查 / 后台 task 等）
- 想快速起一个最小服务专门用来改 .env

与主 chat_api 的区别：
- 端口默认 8765，避开主服务的 8000
- 进程只挂载 /config 子应用，没有 /v1/chat/completions 等业务路由
- 启动只需要不到 1 秒，无外部依赖
"""

from __future__ import annotations

import argparse
import sys

from fastapi import FastAPI

from xuwen.cli_i18n import SUPPORTED_LANGUAGES, cli_text
from xuwen.config import LanguageSettings, get_settings
from xuwen.web_ui import create_config_app


def build_standalone_app(
    *, base_url: str = "http://127.0.0.1:8765", language: str | None = None
) -> FastAPI:
    """构造独立模式 app。

    base_url 用于启动 banner 显示的访问地址。
    """
    settings = get_settings()
    if language in SUPPORTED_LANGUAGES:
        settings = settings.model_copy(update={"xuwen_language": language})
    app = FastAPI(
        title=cli_text("config.title", settings.xuwen_language),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    config_app = create_config_app(settings, base_url=base_url)
    app.mount(settings.config_ui_path_prefix, config_app)
    return app


def main() -> None:
    language: str = LanguageSettings().xuwen_language
    for index, arg in enumerate(sys.argv[1:], 1):
        candidate = (
            arg.split("=", 1)[1]
            if arg.startswith("--language=")
            else (
                sys.argv[index + 1] if arg == "--language" and index + 1 < len(sys.argv) else None
            )
        )
        if candidate in SUPPORTED_LANGUAGES:
            language = candidate
    parser = argparse.ArgumentParser(
        prog="python -m xuwen.web_ui",
        description=cli_text("config.description", language),
    )
    parser.add_argument(
        "--language",
        choices=SUPPORTED_LANGUAGES,
        default=language,
        help=cli_text("language.help", language),
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help=cli_text("config.host", language),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help=cli_text("config.port", language),
    )
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError:
        print(cli_text("config.uvicorn_missing", language), file=sys.stderr)
        sys.exit(1)

    base_url = f"http://{args.host}:{args.port}"

    # 用闭包让 banner 拿到正确的 host:port
    def factory() -> FastAPI:
        return build_standalone_app(base_url=base_url, language=args.language)

    uvicorn.run(
        factory,
        factory=True,
        host=args.host,
        port=args.port,
        log_level="warning",  # 配置 UI 不需要 access log 刷屏
    )


if __name__ == "__main__":
    main()
