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

Afterglow v1 是给第三方中间件使用的稳定、平台无关中间格式，目前只支持私聊。可以使用 AI 编写
一次性中间件，将任意来源的聊天记录转换为该格式，以快速复用 Afterglow 的导入流水线。

转换中间件适合验证和个人迁移；长期维护、公开使用或需要完整保留源平台语义时，仍建议实现独立
ingestion plugin 并提交 PR。plugin 可以把格式识别、身份嗅探、消息解析和图片引用提取封装在同一处，
不会因中间格式转换损失信息。

JSON 格式：

```json
{
  "afterglow": {"format": "afterglow-chat", "version": "1.0"},
  "conversation": {"id": "conv-001", "type": "private", "title": "可选"},
  "participants": [
    {"uid": "me", "name": "我", "role": "self"},
    {"uid": "friend", "name": "TA", "role": "friend"}
  ],
  "messages": [
    {
      "id": "m1",
      "seq": 1,
      "timestamp_ms": 1783625485000,
      "sender_uid": "friend",
      "sender_name": "TA",
      "sender_role": "friend",
      "kind": "text",
      "raw_type": "text",
      "text": "今天怎么样"
    }
  ]
}
```

消息字段：

- `timestamp_ms` 必须是毫秒时间戳。
- `sender_role` 可为 `self` / `friend` / `system` / `other`；缺失时按 `participants` 和 `.env` UID 兜底。
- `kind` 复用 `text` / `reply` / `placeholder` / `recalled` / `system` / `unknown`。
- 图片消息在普通文本导入中只保留占位和引用，不调用视觉模型：

```json
{
  "id": "m2",
  "seq": 2,
  "timestamp_ms": 1783625493000,
  "sender_uid": "friend",
  "sender_role": "friend",
  "kind": "placeholder",
  "raw_type": "image",
  "text": "[图片: a.jpg]",
  "placeholders": ["[图片]"],
  "attachments": [{"type": "image", "name": "a.jpg"}]
}
```

图片应放在导出目录的 `resources/images/` 下：

```text
export-dir/
  chat.json
  resources/
    images/
      a.jpg
```

文本导入完成后，手动运行图片导入：

```bash
uv run python -m xuwen.ingestion.cli import-images export-dir --plugin afterglow_v1
```

JSONL 同时支持两种形式。推荐使用 `_type=header/participant/message` 的 typed records：

```jsonl
{"_type":"header","afterglow":{"format":"afterglow-chat","version":"1.0"},"conversation":{"id":"conv-001","type":"private"}}
{"_type":"participant","uid":"me","name":"我","role":"self"}
{"_type":"participant","uid":"friend","name":"TA","role":"friend"}
{"_type":"message","id":"m1","seq":1,"timestamp_ms":1783625485000,"sender_uid":"friend","sender_name":"TA","kind":"text","text":"今天怎么样"}
```

也可以每行直接放一个 message 对象。裸 message JSONL 的每一行都必须包含
`sender_uid`（或 `senderUid`）以及 `timestamp_ms`（或 `timestamp`）；由于没有
`participants`，身份必须由消息的 `sender_role`、`.env` 中的 UID，或配置向导中的身份分配确定。

一次导入可以传入多个 Afterglow JSONL：

```bash
uv run python -m xuwen.ingestion.cli import \
  chunk-001.jsonl chunk-002.jsonl \
  --plugin afterglow_v1
```

多文件导入当前是批量逐文件处理，而不是先拼接为一个逻辑 JSONL 流：

- 每个 typed JSONL 文件必须自包含一个 `header`，并且只能在其后包含 `participant` 和 `message`；建议每个分片重复完整的 `participants`。
- bare message 分片可以独立导入，但必须满足上述逐行必填字段和身份配置要求。
- `message.id` 应在整个导出包中稳定且全局唯一。不要依赖 loader 按文件名和行号生成的兜底 ID，否则文件改名、重新分片或 WebUI 重新上传后无法保证幂等去重。
- 文件边界也是会话切分边界；窗口和 response pair 不会跨文件生成，`conversation.id` 当前不会触发分片合并。
- WebUI 会合并本次任务的全部文件生成人格、风格和作息画像；CLI 批量导入也会基于本次传入的全部文件重建作息画像。主动开聊画像会在批量导入完成后基于全部已入库窗口重建。

如果多个 chunk 属于同一段连续对话，并且需要保留跨 chunk 的上下文、窗口和问答关系，应由转换中间件先按时间排序、去重并合并成一个自包含的 typed JSONL，再交给 Afterglow 导入。

上述限制针对文本向量入库。独立的 persona 聚合器会先跨文件去重，再按 ingestion plugin 分组，
在每组内部按 `(timestamp_ms, seq, message_id, sender_uid)` 排序并重新切分会话。因此同格式 chunk
边界两侧的消息可以形成画像回复样本；不同 plugin 的消息会共同参与词频和作息统计，但不会组成
跨平台 response pair。调用方式：

```bash
uv run python scripts/analyze_persona.py \
  chunk-001.jsonl chunk-002.jsonl
```

所有画像输入必须属于同一个目标人物。跨平台或多账号时，应在 `.env` 的 `SELF_UID` / `FRIEND_UID`
中配置该人物对应的全部 UID；不同人物必须分开生成画像。

`import-images` 会按图片 bytes 计算 SHA-256 去重，把原图保存到 `.data/images/<sha>.<ext>`，
对每个唯一 SHA 只调用一次 `VISION_MODEL` 生成摘要，再把“图片摘要 + 消息时间/发送者/上下文 +
image_sha”写入 `history_images` 表。召回时默认使用已生成的图片摘要，不会在聊天请求里重复把历史
原图发给主模型或 VLM。

QQChatExporter 普通文本导入建议在高级选项勾选“仅保留文件元数据，不下载文件”；这只适合轻量
文本入库，会保留图片文件名引用但不会生成 `resources/images` 原图目录。只有需要运行
`import-images` 时，提交目录才必须实际包含 `resources/images` 下的图片文件。

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

## 提交前检查

提交前确认：

- 没有提交 `backend/.env`、`backend/.data/`、聊天导出 JSON、真实 API key。
- 新配置已写入 `.env.example`。
- 新接口已更新 `docs/API.md`。
- 新导入格式已写测试和插件说明。
- 相关测试、ruff、mypy 已通过。
