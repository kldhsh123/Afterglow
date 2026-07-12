# 快速开始

## 环境要求

| 工具 | 要求 | 用途 |
|---|---|---|
| Python | 3.12 或更高 | 后端运行时 |
| [uv](https://github.com/astral-sh/uv) | 当前稳定版 | Python 依赖管理 |
| Node.js | 20 或更高 | 仅构建前端时需要 |
| pnpm | 当前稳定版 | 仅主聊天前端需要 |

Afterglow 不内置模型。至少需要一个 OpenAI 兼容聊天模型和一个 Embedding 服务。
聊天记录可来自 QQChatExporter、WeFlow 或 Afterglow Chat 中间格式，详见
[导入聊天记录](Importing-Chat-History.md)。

## 推荐：使用配置向导

首次启动不需要提前创建 `.env`：

```bash
cd backend
uv sync --extra dev
uv run uvicorn xuwen.chat_api.app:create_app --factory --reload
```

缺少关键配置时，终端会输出配置地址和一次性 token。打开
`http://127.0.0.1:8000/config/`，输入 token 后完成以下步骤：

1. 从一个或多个聊天文件识别双方身份。
2. 选择关系和 persona 模板。
3. 配置并测试主聊天模型。
4. 配置并测试 Embedding，可选启用打标。
5. 选择生活时间线、视觉理解、联网搜索和互动决策等可选能力。
6. 配置 Query Rewrite 与 Cross-encoder Reranker。
7. 导入聊天记录并生成 persona、风格与作息画像。
8. 设置后端访问密码 `XUWEN_API_KEY`。

向导会写入 `backend/.env`，旧文件备份到 `.env-backups/`。完成后停止并重新启动后端，
配置向导会自动关闭。

只需要修改配置时，可启动轻量入口：

```bash
cd backend
uv run python -m xuwen.web_ui
```

浏览器访问 `http://127.0.0.1:8765/config/`。

## 手动配置

```bash
cd backend
uv sync --extra dev
cp .env.example .env
```

至少填写身份、聊天模型、Embedding 和 API 密钥。字段说明见[配置参考](Configuration.md)。
然后导入历史聊天：

```bash
uv run python -m xuwen.ingestion.cli import 路径/到/聊天记录.json
uv run python scripts/analyze_persona.py 路径/到/聊天记录.json
uv run uvicorn xuwen.chat_api.app:create_app --factory --reload
```

多文件、JSONL、微信、图片和 persona 合并说明见[导入聊天记录](Importing-Chat-History.md)。

## 启动前端（可选）

主聊天前端用于本地聊天、记忆溯源和调试：

```bash
cd frontend
pnpm install
pnpm dev
```

打开 `http://127.0.0.1:5173/`。只使用后端 API 时不需要安装前端依赖。

## 接入 OpenAI 兼容客户端

Afterglow 提供 Chat Completions 与 Responses API。最小请求：

```bash
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <XUWEN_API_KEY>" \
  -d '{
    "messages": [{"role": "user", "content": "在吗"}],
    "conversation_id": "my-conv-1"
  }'
```

客户端传入的 `model` 只是兼容字段，实际模型由 `.env` 的 `CHAT_MODEL` 决定。完整协议见
[后端 API](API.md)。

## Docker

不希望安装 Python 和 uv 时，使用[Docker 部署](Docker.md)。源码与容器可以共享同一份
`.env` 和 `.data/`，但不要同时启动以免端口冲突。

## 健康检查

```bash
curl http://127.0.0.1:8000/healthz
curl -H "Authorization: Bearer <XUWEN_API_KEY>" \
  http://127.0.0.1:8000/readyz
```

`/healthz` 是唯一默认免鉴权端点。

