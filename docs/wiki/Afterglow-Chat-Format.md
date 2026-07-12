# Afterglow Chat v1 格式

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
