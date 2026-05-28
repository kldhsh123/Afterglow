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

from xuwen.config import get_settings
from xuwen.web_ui import create_config_app


def build_standalone_app(*, base_url: str = "http://127.0.0.1:8765") -> FastAPI:
    """构造独立模式 app。

    base_url 用于启动 banner 显示的访问地址。
    """
    settings = get_settings()
    app = FastAPI(
        title="Afterglow 配置 UI（独立模式）",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    config_app = create_config_app(settings, base_url=base_url)
    app.mount(settings.config_ui_path_prefix, config_app)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m xuwen.web_ui",
        description=(
            "独立启动 Afterglow 配置 UI（不跑主 chat API）。"
            "适合升级后改配置、或想要最小启动开销的场景。"
        ),
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="监听地址，默认 127.0.0.1（仅本机）。生产场景请保持默认。",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="监听端口，默认 8765（避开主服务的 8000）。",
    )
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError:
        print("缺少 uvicorn 依赖，请运行 uv sync 安装。", file=sys.stderr)
        sys.exit(1)

    base_url = f"http://{args.host}:{args.port}"

    # 用闭包让 banner 拿到正确的 host:port
    def factory() -> FastAPI:
        return build_standalone_app(base_url=base_url)

    uvicorn.run(
        factory,  # type: ignore[arg-type]
        factory=True,
        host=args.host,
        port=args.port,
        log_level="warning",  # 配置 UI 不需要 access log 刷屏
    )


if __name__ == "__main__":
    main()
