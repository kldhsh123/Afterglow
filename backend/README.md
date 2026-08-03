# Afterglow（续温）后端

Afterglow 的核心服务位于 `backend/`：聊天记录导入、LanceDB 记忆、混合检索、persona、生活状态、
关系记忆、图片理解和 OpenAI 兼容 API 都由此提供。`frontend/` 是可选的本地聊天与调试界面。

完整文档：

- [快速开始](https://github.com/kldhsh123/Afterglow/wiki/快速开始)
- [配置参考](https://github.com/kldhsh123/Afterglow/wiki/配置参考)
- [后端环境变量](https://github.com/kldhsh123/Afterglow/wiki/后端环境变量)
- [导入聊天记录](https://github.com/kldhsh123/Afterglow/wiki/导入聊天记录)
- [后端 API](https://github.com/kldhsh123/Afterglow/wiki/后端API文档)
- [故障排查](https://github.com/kldhsh123/Afterglow/wiki/故障排查)
- [开发文档](https://github.com/kldhsh123/Afterglow/wiki/开发文档)

## 数据隐私

聊天记录、向量、persona 和图片默认保存在本机 `.data/`，但云端模型配置会把相关文本发送给对应
provider。不要提交 `.env`、聊天导出、`.data/` 或 API key。完整边界见
[负责任使用与数据隐私](https://github.com/kldhsh123/Afterglow/wiki/负责任使用与数据隐私)。

## 环境

- Python 3.12+
- [uv](https://github.com/astral-sh/uv)
- OpenAI 兼容聊天模型
- OpenAI 兼容 Embedding 服务
- 至少一份受支持的聊天 JSON/JSONL

## 配置向导（推荐）

```bash
uv sync --extra dev
uv run uvicorn xuwen.chat_api.app:create_app --factory --reload
```

缺少关键配置时，终端会打印一次性 token。打开 `http://127.0.0.1:8000/config/` 完成配置、导入和
persona 生成，然后重启后端。关系分析、历史图片导入等高级功能使用 CLI 或主 API 手动运行。

只启动轻量配置入口：

```bash
uv run python -m xuwen.web_ui
```

## 手动配置

```bash
uv sync --extra dev
cp .env.example .env
```

编辑 `.env`，至少填写：

- `SELF_NAME` / `SELF_UID`
- `FRIEND_NAME` / `FRIEND_UID`
- `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `CHAT_MODEL` / `CHAT_API_PROTOCOL`
- `EMBEDDING_API_URL` / `EMBEDDING_API_KEY` / `EMBEDDING_MODEL` / `EMBEDDING_DIM`
- `XUWEN_API_KEY`

同一个人的多平台或多账号 UID 用逗号分隔。修改 `.env` 后必须重启进程。

## 如何找到 UID

推荐在配置向导中上传聊天文件并选择“设为我 / 设为朋友”。CLI 用户可从导出文件获取：

- QQChatExporter：`chatInfo.selfUid` 是自己；消息的 `sender.uid` 是发送者。
- WeFlow：`wxid` / `platformId` 是微信身份。
- Douyin Chat Export：`meta.ownerId` 是自己；`members[].platformId` 是候选身份。
- Afterglow Chat：使用 `participants[].uid` 与 `role`。

不要把不同人物的 UID 合并到同一份 `FRIEND_UID`。

## 常用命令

```bash
# 导入一个或多个 JSON/JSONL
uv run python -m xuwen.ingestion.cli import chat.json chunks/*.jsonl

# 合并全部文件生成 persona、风格和作息画像
uv run python scripts/analyze_persona.py chat.json chunks/*.jsonl

# 可选：从原始聊天文件生成关系时间线和性格报告
uv run python -m xuwen.ingestion.cli analyze chat.json chunks/*.jsonl

# 主动消息倾向不会随关系分析自动运行，需单独执行并检查质量
uv run xuwen analyze-proactive chat.json chunks/*.jsonl
uv run python scripts/check_proactive_quality.py

# 可选：导入历史图片
uv run python -m xuwen.ingestion.cli import-images export-dir

# 续跑标签、查看统计、构建索引、优化表
uv run python -m xuwen.ingestion.cli label
uv run python -m xuwen.ingestion.cli stats
uv run python -m xuwen.ingestion.cli index
uv run python -m xuwen.ingestion.cli optimize
```

分析会缓存已成功的块。网络、鉴权等请求错误会停止任务；模型拒绝或连续返回无效 JSON 的单个块会
写入 `failures/` 并跳过。重新运行时默认复用已有缓存。开启实验分析后，每个块会增加一次独立的
实验信号提取请求。

## 启动 API

```bash
uv run uvicorn xuwen.chat_api.app:create_app --factory --reload
```

- `POST /v1/chat/completions`
- `POST /v1/responses`
- `GET /healthz`：唯一默认免鉴权端点
- `GET /readyz`
- `GET /info`
- `GET /memory/stats`

除 `/healthz` 外，接口默认使用 `Authorization: Bearer <XUWEN_API_KEY>`。

## 代码结构

```text
xuwen/
├── ingestion/   # plugin、清洗、切分、向量化与导入
├── analysis/    # 离线关系时间线、性格报告与隔离实验性观察
├── memory/      # LanceDB schema、检索与回写
├── persona/     # persona、风格、作息与标签
├── companion/   # 生活状态、关系记忆与互动决策
├── chat_api/    # FastAPI 与 OpenAI 兼容协议
└── web_ui/      # 配置向导后端与构建产物
```

配置向导源码位于 `web_ui_src/`，构建产物写入 `xuwen/web_ui/static/`。

## 开发检查

```bash
uv run ruff check xuwen scripts
uv run mypy xuwen
uv run pytest tests/unit -q
```

配置向导前端：

```bash
cd web_ui_src
npm run build
```

## License

AGPL-3.0-or-later
