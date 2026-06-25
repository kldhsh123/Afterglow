# 开发文档

本文面向想参与 Afterglow 开发、添加导入格式、调试后端能力或提交 PR 的贡献者。

## 环境准备

后端：

```bash
cd backend
uv sync --extra dev
cp .env.example .env
```

前端：

```bash
cd frontend
pnpm install
```

启动后端：

```bash
cd backend
uv run uvicorn xuwen.chat_api.app:create_app --factory --reload
```

启动前端：

```bash
cd frontend
pnpm dev
```

## 后端结构

```text
backend/xuwen/
├── chat_api/      FastAPI 服务、路由、LLM/VLM/联网客户端、调试指标
├── companion/     AI 生活状态、关系记忆
├── core/          通用模型、错误、时间、指标
├── ingestion/     导入 JSON、清洗、切分、向量化、打标
├── memory/        LanceDB schema、读写、混合检索、回写队列
└── persona/       persona 分析、prompt 模板、语义打标
```

核心请求链路：

1. `POST /v1/chat/completions` 接收 OpenAI 兼容请求。
2. 如果消息带图片，根据视觉配置处理图片。
3. `HybridRetriever` 用当前文本检索历史。
4. `LifeStateManager` 更新或读取 AI 当前生活状态。
5. `RelationshipMemoryManager` 提供关系记忆摘要。
6. 可选 `WebSearchClient` / `WebFetchClient` 注入公开网页上下文。
7. `build_chat_messages()` 组装 system prompt。
8. `LLMClient` 调用上游模型。
9. 如果有 `conversation_id`，把这一轮写入 live memory。

## 导入插件开发

导入系统已经按插件拆开。主流程只需要统一的 `NormalizedMessage`，不关心消息来自 QQ、微信还是其它平台。

目前内置 plugin：

| name | display_name | 输入格式 | 识别特征 |
|---|---|---|---|
| `qqexporter_v5` | QQChatExporter V5 | QQ 导出 JSON | `metadata.name` 含 `qqchatexporter` 或 `chatInfo.selfUid` 存在 |
| `wechat_weflow` | WeChat (WeFlow arkme-json) | 微信 WeFlow 导出 JSON | `weflow.format = "arkme-json"` 或 `session + senders + messages` 同时存在 |

> **微信导入提醒**：WeFlow 是 Afterglow 所支持的微信导入适配器，Afterglow 的默认微信导入插件依赖此项目。
> 我注意到 WeFlow 不再开源，所以我无法保证 WeFlow 将来的安全性。
> 所以在不久的将来我需要 WeFlow 的替代方案来确保用户隐私安全。
> 在此期间，我不会建议使用 WeFlow，但这是目前唯一可用的方案。

CLI 在导入时按注册顺序遍历 `match()`，第一个命中的负责 `parse()`；
也可以用 `--plugin <name>` 强制指定。

插件接口在 `backend/xuwen/ingestion/plugins/__init__.py`：

```python
class ImportPlugin(Protocol):
    name: str
    display_name: str

    def match(self, payload: dict[str, Any]) -> bool:
        ...

    def parse(
        self,
        payload: dict[str, Any],
        settings: Settings,
    ) -> list[NormalizedMessage]:
        ...
```

新增一个导入格式的步骤：

1. 在 `backend/xuwen/ingestion/plugins/` 下新增模块，例如 `wechat_xxx.py`。
2. 实现 `name`、`display_name`、`match()`、`parse()`。
3. 在 `backend/xuwen/ingestion/parser.py` 注册插件。
4. 添加单元测试，覆盖自动识别、强制指定和关键消息类型。
5. 用真实脱敏样例跑一次导入。

最小示例：

```python
from typing import Any

from xuwen.config import Settings
from xuwen.core.models import MessageKind, NormalizedMessage, SenderRole


class ExamplePlugin:
    name = "example"
    display_name = "Example Export"

    def match(self, payload: dict[str, Any]) -> bool:
        return payload.get("format") == "example"

    def parse(
        self,
        payload: dict[str, Any],
        settings: Settings,
    ) -> list[NormalizedMessage]:
        messages: list[NormalizedMessage] = []
        for idx, raw in enumerate(payload.get("messages") or []):
            sender_uid = str(raw.get("sender_id") or "")
            role = (
                SenderRole.SELF
                if sender_uid == settings.self_uid
                else SenderRole.FRIEND
            )
            messages.append(
                NormalizedMessage(
                    message_id=str(raw.get("id") or idx),
                    seq=idx,
                    timestamp_ms=int(raw.get("timestamp_ms") or 0),
                    sender_uid=sender_uid,
                    sender_name=str(raw.get("sender_name") or ""),
                    sender_role=role,
                    kind=MessageKind.TEXT,
                    raw_type=str(raw.get("type") or "text"),
                    text=str(raw.get("text") or ""),
                    raw=raw,
                )
            )
        return messages
```

注意事项：

- `match()` 必须轻量，不做文件 IO，不发网络请求。
- `parse()` 不要直接写库、不要调用 embedding、不要改全局状态。
- 解析失败的单条消息可以跳过，但不要吞掉整体格式错误。
- `timestamp_ms` 必须是毫秒时间戳。
- `sender_role` 必须正确区分 `SELF` 和 `FRIEND`，否则检索会把用户自己的话当成对方风格。
- 图片、表情、语音等无法转文字的内容应放入 `placeholders`，正文可保留 `[图片]` 等占位。

查看当前插件：

```bash
cd backend
uv run python -m xuwen.ingestion.cli plugins
```

强制使用某插件导入：

```bash
uv run python -m xuwen.ingestion.cli import export.json --plugin qqexporter_v5
uv run python -m xuwen.ingestion.cli import export.json --plugin wechat_weflow
```

## 测试与质量检查

后端常用命令：

```bash
cd backend
uv run ruff check xuwen
uv run mypy xuwen
```

前端构建：

```bash
cd frontend
pnpm build
```

## 调试建议

运行时诊断：

```bash
curl http://127.0.0.1:8000/debug/stats
curl http://127.0.0.1:8000/debug/config
```

重点看：

- `model_chain`：模型请求完整链路。
- `life`：AI 当前生活状态和最近决策。
- `database`：LanceDB 读写耗时。
- `calls`：LLM、retrieval、web search、web fetch 等调用统计。

检索调试：

```bash
curl -X POST http://127.0.0.1:8000/memory/search \
  -H "Content-Type: application/json" \
  -d '{"query":"你在干嘛","top_k":12}'
```

## 配置开发原则

- 新增运行时配置应放进 `Settings`，不要在业务代码硬编码。
- `.env.example` 必须同步更新。
- 默认值必须保守，尤其是联网、视觉、写回、调试端点等涉及隐私或外部请求的能力。
- API key 使用 `SecretStr`，日志和调试接口不能输出明文。
- 任何“读取 URL”的能力都必须做 SSRF 防护：拒绝本机/内网/特殊地址，限制跳转、超时、响应大小和 prompt 注入长度。

## 文档与注释风格

项目面向中文用户，新增文档、注释、docstring 默认使用中文。

代码命名保持英文，因为 Python 生态和类型工具更适合英文标识符；但解释性文字、错误信息、README、开发文档应优先中文。

## 提交前检查

提交前确认：

- 没有提交 `backend/.env`、`backend/.data/`、聊天导出 JSON、真实 API key。
- 新配置已写入 `.env.example`。
- 新接口已更新 `docs/API.md`。
- 新导入格式已写测试和插件说明。
- 相关测试、ruff、mypy 已通过。
