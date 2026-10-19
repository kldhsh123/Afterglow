[简体中文](README.md) | [English documentation](https://kldhsh123.github.io/Afterglow/en/)

# Afterglow（续温）后端

Afterglow 的核心服务位于 `backend/`：聊天记录导入、LanceDB 记忆、混合检索、persona、生活状态、
关系记忆、图片理解和 OpenAI 兼容 API 都由此提供。`frontend/` 是可选的本地聊天与调试界面。

完整文档：

- [快速开始](https://kldhsh123.github.io/Afterglow/zh/quickstart.html)
- [配置参考](https://kldhsh123.github.io/Afterglow/zh/configuration.html)
- [后端环境变量](https://kldhsh123.github.io/Afterglow/zh/environment.html)
- [导入聊天记录](https://kldhsh123.github.io/Afterglow/zh/importing.html)
- [后端 API](https://kldhsh123.github.io/Afterglow/zh/api.html)
- [故障排查](https://kldhsh123.github.io/Afterglow/zh/troubleshooting.html)
- [开发文档](https://kldhsh123.github.io/Afterglow/zh/development.html)

## 数据隐私

聊天记录、向量、persona 和图片默认保存在本机 `.data/`，但云端模型配置会把相关文本发送给对应
provider。不要提交 `.env`、聊天导出、`.data/` 或 API key。完整边界见
[负责任使用与数据隐私](https://kldhsh123.github.io/Afterglow/zh/privacy.html)。

## 环境

- Python 3.12.x（`>=3.12,<3.13`）
- [uv](https://github.com/astral-sh/uv)
- OpenAI 兼容聊天模型
- OpenAI 兼容 Embedding 服务
- 至少一份受支持的聊天 JSON/JSONL

## 配置向导（推荐）

```bash
uv sync --extra dev
XUWEN_LANGUAGE=zh-CN uv run uvicorn xuwen.chat_api.app:create_app --factory --reload
```

缺少关键配置时，终端会打印一次性 token。打开 `http://127.0.0.1:8000/config/` 完成配置、导入和
persona 生成，然后重启后端。关系分析、历史图片导入等高级功能使用 CLI 或主 API 手动运行。

只启动轻量配置入口：

```bash
uv run python -m xuwen.web_ui --language zh-CN
```

后端默认英文。本页用 `XUWEN_LANGUAGE=zh-CN` 指定中文启动；CLI 使用 `--language zh-CN` 或 `--language en`，HTTP 请求使用 `Accept-Language: zh-CN` 或 `Accept-Language: en`。界面下拉框只切换界面。Bash、PowerShell 写法及语言优先级见[快速开始](https://kldhsh123.github.io/Afterglow/zh/quickstart.html)。

## 手动配置

```bash
uv sync --extra dev
cp .env.example.zh-CN .env
```

编辑 `.env`，至少填写：

- `SELF_NAME` / `SELF_UID`
- `FRIEND_NAME` / `FRIEND_UID`
- `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `CHAT_MODEL` / `CHAT_API_PROTOCOL`
- `EMBEDDING_API_URL` / `EMBEDDING_API_KEY` / `EMBEDDING_MODEL` / `EMBEDDING_DIM`
- `XUWEN_API_KEY`
- `XUWEN_LANGUAGE=zh-CN`（中文输出；模板默认 `en`）

同一个人的多平台或多账号 UID 用逗号分隔。修改 `.env` 后必须重启进程。

## 如何找到 UID

推荐在配置向导中上传聊天文件并选择“设为我 / 设为朋友”。CLI 用户可从导出文件获取：

- QQChatExporter：`chatInfo.selfUid` 是自己；消息的 `sender.uid` 是发送者。
- WeFlow：`wxid` / `platformId` 是微信身份。
- Douyin Chat Export：`meta.ownerId` 是自己；`members[].platformId` 是候选身份。
- Afterglow Chat：使用 `participants[].uid` 与 `role`。

不要把不同人物的 UID 合并到同一份 `FRIEND_UID`。

## 常用命令

`scripts/analyze_persona.py` 和 `scripts/check_proactive_quality.py` 是独立辅助脚本，目前没有 `--language` 参数，部分控制台提示仍为中文。不要给它们追加该参数；需要选择语言的导入或分析任务请使用主 CLI 的 `--language zh-CN` / `--language en`。

```bash
# 导入一个或多个 JSON/JSONL
uv run python -m xuwen.ingestion.cli import chat.json chunks/*.jsonl --language zh-CN

# 合并全部文件生成 persona、风格和作息画像
uv run python scripts/analyze_persona.py chat.json chunks/*.jsonl

# 可选：从原始聊天文件生成关系时间线和性格报告
uv run python -m xuwen.ingestion.cli analyze chat.json chunks/*.jsonl --language zh-CN

# 主动消息倾向不会随关系分析自动运行，需单独执行并检查质量
uv run xuwen analyze-proactive chat.json chunks/*.jsonl --language zh-CN
uv run python scripts/check_proactive_quality.py

# 可选：导入历史图片
uv run python -m xuwen.ingestion.cli import-images export-dir --language zh-CN

# 续跑标签、查看统计、构建索引、优化表
uv run python -m xuwen.ingestion.cli label --language zh-CN
uv run python -m xuwen.ingestion.cli stats --language zh-CN
uv run python -m xuwen.ingestion.cli index --language zh-CN
uv run python -m xuwen.ingestion.cli optimize --language zh-CN
```

分析会缓存已成功的块。网络、鉴权等请求错误会停止任务；模型拒绝或连续返回无效 JSON 的单个块会
写入 `failures/` 并跳过。重新运行时默认复用已有缓存。开启实验分析后，每个块会增加一次独立的
实验信号提取请求。

## 启动 API

```bash
XUWEN_LANGUAGE=zh-CN uv run uvicorn xuwen.chat_api.app:create_app --factory --reload
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

配置模板：[英文注释](.env.example) · [中文注释](.env.example.zh-CN)；也可通过[双语文档站配置编辑器](https://kldhsh123.github.io/Afterglow/zh/config-editor.html)在浏览器中生成 `.env`。
