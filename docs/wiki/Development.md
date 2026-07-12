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
3. `HybridRetriever` 用当前文本执行五路向量召回，并读取当前会话 Recent Live。
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
| `afterglow_v1` | Afterglow Chat v1 | Afterglow 专用私聊 JSON/JSONL | JSON metadata 或 typed/bare JSONL records |
| `qqexporter_v5` | QQChatExporter | QQChatExporter JSON/JSONL | metadata/chatInfo 或 JSONL message records |
| `wechat_weflow` | WeChat (WeFlow) | [WeFlow Releases](https://github.com/hicccc77/weflow-releases/) 导出 JSON/JSONL | `arkme-json` 或 ChatLab typed JSONL records |

> **微信导入提醒**：[WeFlow Releases](https://github.com/hicccc77/weflow-releases/) 当前发布版不是开源软件，
> Afterglow 无法审计或担保。开发和测试时不要使用未脱敏的真实聊天数据。

CLI 在导入时按注册顺序遍历 `match()`，第一个命中的负责 `parse()`；
也可以用 `--plugin <name>` 强制指定。

通用 loader 只负责把 JSONL 解码为中性 records，不包含任何平台字段判断。plugin 使用
`jsonl_records(payload)` 取得逐行对象，并在自身模块内完成格式识别与规范化。

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

配置向导身份识别和历史图片引用是独立可选能力。需要这些功能的 plugin 还应实现：

```python
class InspectableImportPlugin(Protocol):
    def inspect(self, payload: dict[str, Any]) -> ImportInspection: ...

class ImageReferenceImportPlugin(Protocol):
    def extract_image_refs(self, payload: dict[str, Any]) -> list[ImportImageRef]: ...
```

格式名称、候选身份和图片字段解释都应留在 plugin 内；不要在 `parser.py`、WebUI 或
`image_importer.py` 添加平台专用分支。

新增一个导入格式的步骤：

1. 在 `backend/xuwen/ingestion/plugins/` 下新增模块，例如 `wechat_xxx.py`。
2. 实现 `name`、`display_name`、`match()`、`parse()`。
3. 需要向导自动识别身份时实现 `inspect()`；需要历史图片导入时实现 `extract_image_refs()`。
4. 在 `backend/xuwen/ingestion/parser.py` 注册插件。
5. 添加单元测试，覆盖自动识别、强制指定和关键消息类型。
6. 用真实脱敏样例跑一次导入。

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
            role: SenderRole = "self" if sender_uid == settings.self_uid else "friend"
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
uv run python -m xuwen.ingestion.cli import export.jsonl --plugin afterglow_v1
```

### Afterglow Chat v1

Afterglow 专用 JSON / JSONL 中间格式已经独立为
[Afterglow Chat v1 格式](Afterglow-Chat-Format.md)。本页只保留 ingestion plugin 的开发约定。


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

调试端点默认关闭。需要运行时诊断时，在 `backend/.env` 里临时设置
`DEBUG_ENDPOINTS_ENABLED=true` 并重启后端；排查完成后建议关回 `false`。

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

长文档统一放在 `docs/wiki/`，并同步更新 `Home.md` 和 `_Sidebar.md`。GitHub Wiki 是 Actions
生成的镜像，不要直接在 Wiki 网页修改。具体规则见
[文档与 Wiki 维护](Documentation-Maintenance.md)。

## 提交前检查

提交前确认：

- 没有提交 `backend/.env`、`backend/.data/`、聊天导出 JSON、真实 API key。
- 新配置已写入 `.env.example`。
- 新接口已更新 `docs/wiki/API.md`。
- 新文档页面已加入 `docs/wiki/Home.md` 和 `_Sidebar.md`。
- 新导入格式已写测试和插件说明。
- 相关测试、ruff、mypy 已通过。
