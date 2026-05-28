# syntax 指令默认走 docker-desktop 内置 BuildKit frontend；
# 不显式锁版本是为了避免 `docker/dockerfile:1.x` 镜像拉取受凭据助手故障影响。
# 本文件使用的特性（--mount=type=cache / COPY --chmod / 多阶段 --from）
# 在内置 frontend 已稳定支持。
# ============================================================
# Afterglow（续温）后端镜像
# - 多阶段构建：builder 装依赖，runtime 只携带 .venv + 业务代码
# - 用 uv 锁定依赖；BuildKit 缓存复用 ~/.cache/uv
# - 运行时 non-root；HEALTHCHECK 打 /healthz
# - 与源码部署完全等价：唯一入口 `uvicorn xuwen.chat_api.app:create_app --factory`
# 构建上下文须为仓库根目录（compose.yaml 默认 context: .）
# ============================================================

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.5

# ------------------------------------------------------------
# Stage 0: uv 二进制源（用命名阶段让 ARG 替换生效；
# BuildKit 不支持在 COPY --from=<image>:${ARG} 里直接展开变量）
# ------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ------------------------------------------------------------
# Stage 1: builder —— 编译/安装依赖
# ------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/backend/.venv

# pyarrow / lancedb 的 wheel 通常已预编译，但保留 build-essential 以兜底源码 fallback
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 从官方 uv 镜像拷贝二进制（避免 pip 安装 uv 的版本漂移）
COPY --from=uv /uv /uvx /usr/local/bin/

WORKDIR /app/backend

# 1) 先只拷依赖描述，最大化 Docker layer 缓存命中率
COPY backend/pyproject.toml backend/uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# 2) 拷源码并把项目本身装入 venv
COPY backend/xuwen ./xuwen
COPY backend/scripts ./scripts
COPY backend/README.md ./README.md
COPY backend/.env.example ./.env.example
COPY VERSION /app/VERSION

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ------------------------------------------------------------
# Stage 2: runtime —— 最终镜像
# ------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/backend/.venv/bin:$PATH" \
    VIRTUAL_ENV="/app/backend/.venv"

# lancedb / pyarrow 运行时需要 libgomp1；curl 用于 HEALTHCHECK
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1000 afterglow \
    && useradd  --system --uid 1000 --gid afterglow \
                --create-home --home-dir /home/afterglow afterglow

WORKDIR /app/backend

COPY --from=builder --chown=afterglow:afterglow /app /app

# 入口脚本：处理冷启动自举（详见脚本头注释）
COPY --chmod=0755 docker/entrypoint.sh /usr/local/bin/afterglow-entrypoint

# .data 目录由 volume 挂载提供；预创建确保挂载点权限正确
RUN mkdir -p /app/backend/.data \
 && chown -R afterglow:afterglow /app/backend/.data

# 注意：以 root 启动 entrypoint，脚本会做 uid/gid 对齐后再 exec 到 afterglow。
# 不能在这里 USER afterglow——否则 usermod 没权限。

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

ENTRYPOINT ["afterglow-entrypoint"]

# 与源码部署的 `uv run uvicorn xuwen.chat_api.app:create_app --factory --reload` 等价
# （生产去掉 --reload；监听 0.0.0.0 以接收容器外流量）
CMD ["uvicorn", "xuwen.chat_api.app:create_app", \
     "--factory", "--host", "0.0.0.0", "--port", "8000"]
