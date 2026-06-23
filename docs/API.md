# 后端 API 文档

Afterglow 后端是一个 FastAPI 服务。主要集成入口是 OpenAI 兼容的
`/v1/chat/completions`；其它接口用于主动发起话题、记忆调试、本地初始化、
表情包、图片、文档提取和诊断。

默认地址：

```text
http://127.0.0.1:8000
```

默认强制 API key 鉴权。除 `/healthz` 存活检查外，其它接口都需要先在后端
`.env` 设置 `XUWEN_API_KEY`，并在请求里带以下任一鉴权头：

```http
Authorization: Bearer <XUWEN_API_KEY>
x-api-key: <XUWEN_API_KEY>
```

如果 `API_AUTH_REQUIRED=true` 但没有配置 `XUWEN_API_KEY`，受保护接口会返回
`503 xuwen.auth_config`。`API_AUTH_REQUIRED=false` 只建议纯本地开发/测试时临时使用。

所有请求都会带响应头 `x-request-id`。聊天、记忆检索和主动话题接口还会在响应体里返回
`trace_id`，用于在 `/debug/stats` 里追踪完整模型调用链路。

## 核心接口

### `POST /v1/chat/completions`

OpenAI 兼容聊天接口，也是第三方程序最应该接入的主接口。

相比普通 OpenAI Chat Completions，它额外做了这些事：

- 从 LanceDB 检索相关历史记忆。
- 注入 persona、关系记忆、真实当前时间、AI 生活状态、可选联网检索摘要和可选 URL 网页读取结果。
- 在启用视觉配置时支持图片输入。
- 传入 `conversation_id` 时，会把完整一轮对话写回 live memory。
- 可选传入 `caller_id` / `client_message_id`，用于还原 IM 里用户短时间连续发多条消息的打断与合并语义。

> **关于 `model` 字段：** Afterglow 的模型选择是后端运维决策（你在 `.env` 里通过 `CHAT_MODEL` 配置），
> 不应由客户端控制。请求体里的 `model` 字段作为 OpenAI 协议占位**接受但完全忽略**——
> 不管你传什么字符串，后端都会用 `.env` 配的模型。响应体里的 `model` 字段会返回实际使用的模型名。

> **关于 `policy` 字段：** 响应体顶层附带非 OpenAI 字段 `policy`（含 `should_reply` / `reply_mode` /
> `user_state` / `risk_level` / `reason` / `reply_delay_seconds` / `reply_delay_reason`），让调用方识别本轮决策。
> `reply_delay_seconds` 是建议客户端延迟展示回复内容的秒数；后端不再为了拟人化延迟阻塞请求。
> OpenAI 官方 SDK 会忽略它，不影响兼容性。流式 chat 首个 chunk 也会带 `policy`，方便客户端在内容到达前先拿到延迟。
> 当 AI 主动选择不回复时（用户说"别说话"、决策层认为不应继续刺激用户等场景），
> 响应会带 `finish_reason="silenced"` + `content="[silent]"`（sentinel 可通过 `SILENCE_RESPONSE_SENTINEL` 配置）+ `policy.should_reply=false`。

> **关于 `schedule_tasks` 字段：** 响应体顶层另一非 OpenAI 字段，类型为 `list[ScheduleTask] | null`。
> 仅在 `SCHEDULE_EXTRACT_ENABLED=true` 且 AI 解析到用户的定时任务请求时返回；其它情况是 `null`。
> 第三方程序（IM bot / 自动化脚本 / 桌面通知）可凭此字段把"明天早上7点叫我起床"这类自然语言意图直接转为定时任务。
> 每条 `ScheduleTask` 字段：
> - `id` (string)：短随机 ID，便于第三方做幂等去重，例如 `"t_a1b2c3"`
> - `trigger_at` (string)：ISO 8601 含时区的【绝对】首次触发时间，例如 `"2026-06-01T07:00:00+08:00"`
> - `recurrence` (string | null)：iCalendar RRULE 子集，例如 `"FREQ=DAILY;BYHOUR=7;BYMINUTE=0"`；`null` 表示一次性
> - `message` (string)：届时要发送给用户的内容
> - `title` (string)：简短标题（可选）
> - `source` (string)：`"extractor"`（默认，时间线小模型解析）或 `"main"`（主聊天模型直出，目前未启用）
>
> 解析流程：主聊天模型在回复里输出 `<schedule-hint>明天早上7点叫我起床</schedule-hint>`（自然语言意图，对用户不可见），
> 后端调用时间线小模型把每条 hint 转为结构化 `ScheduleTask`。失败/超时/未启用时直接返回 `null`，不影响主回复。
> 小模型配置见 `.env.example` 的 `SCHEDULE_*` 段；留空时依次复用 `LABEL_*` / `RESPONSE_POLICY_*` / `LIFE_*` / 主 LLM。

> 严格 enum 校验的 OpenAI SDK 可在 `.env` 里把 `SILENCE_FINISH_REASON=stop` 退回标准协议。

> **关于用户连发消息（`caller_id` / `client_message_id`）：**
> 这是 Afterglow 的非 OpenAI 扩展字段，默认不启用；旧客户端不传时保持普通 Chat Completions 行为。
>
> - `caller_id`：调用方稳定 ID，例如一个前端会话、一个 IM bot 会话、一个外部程序实例。只有同一个
>   `caller_id` 的请求会互相取消；不同 `caller_id` 可以并行。
> - `client_message_id`：调用方给“这一条用户消息”生成的 ID，用于幂等排队。未传时后端会生成临时 ID，
>   但调用方重试去重能力会变弱。
>
> 推荐接入方式：用户每发一条气泡就立即请求后端，不做 1 秒级 debounce。若同一 `caller_id` 的上一轮
> 还没完成，新请求会把上一轮标记为取消，并把之前尚未成功回复的用户消息与当前消息合并成这一轮的
> `current_user_text`，中间用双换行分隔。成功回复后，这些 `client_message_id` 会被确认并从队列移除。
>
> 使用该模式时，请求体里的 `messages` 建议只放“已经完成回复的历史 + 当前这一条新用户消息”。
> 不要把尚未被回复的旧用户气泡也重复放进 `messages`，否则它们会同时出现在历史和后端未完成队列里。
>
> 被取消的旧请求不会写入 live memory / relationship memory，也不会确认未完成队列。流式旧请求会尽快以
> `finish_reason="cancelled"` 收尾；非流式旧请求如果已经进入上游模型调用，后端不能强行中断远端推理，
> 但返回前会检查自己是否仍是最新 generation，若已被取代则返回空内容 + `finish_reason="cancelled"`。

> **关于多条消息分条（QQ / 微信式"连续发好几条"）：** Afterglow 通过 persona 模板约束主模型把
> "本轮想分多条发出去的内容"用**双换行 `\n\n` 作为分条分隔符**写在同一个 assistant message 里，
> 而**不是**返回多个 choices 或多次 API 调用 —— 这样才能保持 OpenAI 协议 1:1 兼容。
> 单换行 `\n` 仍然是同一条消息内的换行（用于诗、列表、代码等），不构成分条。
>
> **调用方需要自行处理这件事**：
> - **想还原 QQ/微信"分多条气泡"效果**：按 `\n\n` split content，每段当独立消息渲染，
>   段与段之间加 1.5-3 秒随机延迟（模拟人打字的节奏，不要瞬间全部 push）。建议每段渲染前
>   先校验当前会话还没被切走，避免延迟期间用户切走会话后老内容污染新会话。
>   `frontend/src/stores/chat.ts` 的 `finishAssistantMessage` 是一个可参考的实现。
> - **不需要分条效果（CLI / 单气泡 UI / 第三方机器人转发）**：直接把整段 content 渲染出去即可，
>   `\n\n` 在 markdown 渲染器里本就是段落分隔，体验不会比单段差。
> - **流式 SSE**：`\n\n` 可能跨多个 delta chunk 出现（主模型逐 token 输出），客户端应该
>   先把所有 delta 拼起来、流结束后再 split，**不要**在每个 delta 内做 split（会把"段中间"误判成分条）。
> - `policy.reply_delay_seconds` 是**整条回复**展示前的延迟（拟人化"看了一会儿才回"），
>   与上面"段间延迟"是两层不同的延迟，互不冲突；客户端先等 `reply_delay_seconds` 再开始逐段播放。

> **关于 life 状态自更新（`LIFE_MARKER_UPDATE_ENABLED=true`，默认开）：**
> 主模型在回复末尾可输出隐藏标记块 `<life-update>{"current_activity": "...", "recent_meal": "...", "mood": "...", "availability": "..."}</life-update>`，
> 后端解析后**直接** patch AI 的生活时间线（不调小模型，零额外 API 调用），并从对外回复里**剥离**这个块用户看不到。
> 流式过程中标记块也不会被切到中间发出去（output_filter 会缓冲到块结束再统一过滤）。
> 关闭此开关时只剥离不应用，避免前端看到内部协议。

请求示例：

```json
{
  "model": "gpt-4o-mini",
  "messages": [
    {"role": "user", "content": "在吗"}
  ],
  "stream": false,
  "temperature": 0.7,
  "max_tokens": 300,
  "conversation_id": "my-app-user-1",
  "caller_id": "my-app-user-1",
  "client_message_id": "u-20260623-0001"
}
```

响应示例：

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1779400000,
  "model": "gpt-4o-mini",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "在呢，怎么啦"},
      "finish_reason": "stop"
    }
  ],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
  "trace_id": "...",
  "policy": {
    "should_reply": true,
    "reply_mode": "calm",
    "user_state": "normal",
    "risk_level": "low",
    "reason": "按真人历史风格自然短回。",
    "reply_delay_seconds": 0,
    "reply_delay_reason": ""
  }
}
```

流式请求使用 OpenAI 风格 SSE：

```bash
curl -N http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "今天有点累"}],
    "stream": true,
    "conversation_id": "demo"
  }'
```

图片输入遵循 OpenAI 多模态格式：

```json
{
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "看看这张图"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
      ]
    }
  ]
}
```

图片相关配置：

- `VISION_ENABLED=true`
- 如果主聊天模型原生支持视觉：`CHAT_MODEL_SUPPORTS_VISION=true`
- 否则配置 `VISION_API_URL`、`VISION_API_KEY`、`VISION_MODEL`

联网与 URL 读取：

- `WEB_ACCESS_ENABLED=true` 后，用户明确要求“搜索/新闻/最新/天气/价格”等公开实时信息时，后端会调用 Tavily 或 SearXNG，并把摘要注入 prompt。
- 用户消息里包含 `http://`、`https://` 链接且 `WEB_FETCH_ENABLED=true` 时，后端会直接尝试读取网页标题和正文摘录，并把结果注入 prompt。
- 用户只写裸域名（如 `example.com`）时，不会无条件访问。后端会先用本地规则判断是否有“打开/看看/这个网站是什么”等访问意图；命中后再调用小模型确认要访问的候选 URL，确认后按 `https://example.com` 访问。这个小模型复用 `LIFE_API_URL` / `LIFE_API_KEY` / `LIFE_MODEL` 配置。
- URL 读取只支持普通文本网页，不执行 JavaScript；后端会拒绝本机、内网、链路本地等地址，并限制跳转、响应大小和正文字符数。
- 相关诊断在 `/debug/stats` 的 `calls.web.search`、`calls.web.search.skipped`、`calls.web.intent`、`calls.web.fetch`、`calls.web.fetch.skipped`。

### `POST /v1/responses`

OpenAI Responses API 兼容端点（中等子集）。和 `/v1/chat/completions` 共用同一套
检索 / 决策 / 生活状态 / 关系记忆 / 沉默策略，只是协议不同。

**支持字段：**

- `model`（**占位，接受但忽略**——实际用 `.env` 的 `CHAT_MODEL`）
- `input`：字符串或消息数组；多模态 `input_image` 走与 chat 路由相同的视觉链路
- `instructions`：内部转为一条 system message 注入到 prompt 顶部
- `stream`：流式开关，事件按官方 Responses 协议
- `temperature` / `top_p` / `max_output_tokens`
- `previous_response_id`：进程内 LRU 缓存，找到后会沿用上一轮的 `conversation_id`；
  显式传 `conversation_id` 时以显式值为准
- `conversation_id`：Afterglow 扩展，用于关联回写
- `store`：语义忽略（后端始终缓存到 LRU 容量上限，重启即丢失）

**不支持：** `tools` / function-calling / file inputs / `image_generation` /
`code_interpreter` / MCP / `background` 模式。

请求示例：

```bash
curl http://127.0.0.1:8000/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $XUWEN_API_KEY" \
  -d '{
    "instructions": "请用对方的语气陪我聊聊",
    "input": "今天有点累",
    "conversation_id": "demo-1"
  }'
```

非流式响应字段：`id`（resp_xxx）、`object="response"`、`created_at`、`model`、`status="completed"`、`output[]`（含 message item，message 内 `content[]` 是 `output_text`）、`output_text`（便利字段：所有 output_text 拼接）、`usage`、`trace_id`、`policy`（同 chat 接口）、`previous_response_id`。

**流式事件序列**（按官方 Responses 协议）：

```
event: response.created → event: response.in_progress
→ event: response.output_item.added → event: response.content_part.added
→ event: response.output_text.delta（多次） → event: response.output_text.done
→ event: response.content_part.done → event: response.output_item.done
→ event: response.completed → data: [DONE]
```

**沉默响应：** 走完整事件序列，`output_text.delta` 只发一次（值为 `SILENCE_RESPONSE_SENTINEL`，
默认 `"[silent]"`），`status` 为 `completed`；调用方应靠顶层 `policy.should_reply == false`
或 `output_text == sentinel` 识别。

**多条消息分条：** 与 `/v1/chat/completions` 同样的语义 —— 主模型用 `\n\n` 在同一段
`output_text` 内表示"想分多条发出去"。流式时 `\n\n` 可能跨多个 `output_text.delta`，
客户端应先拼接完整 `output_text` 再 split，按段渲染并加 1.5-3s 段间延迟以模拟分条节奏。
详见上面 `/v1/chat/completions` 的"多条消息分条"小节。

**previous_response_id 串接：** 第二次请求带上上次返回的 `id`，后端会沿用上次的
`conversation_id` 自动接上对话。重启进程后缓存丢失，此时建议改用显式 `conversation_id`。

### `POST /v1/companion/proactive`

让 AI 主动开启一个话题。这个接口适合外部调度器、机器人框架或其它程序调用，
效果类似“对方主动找用户聊天”。

请求示例：

```json
{
  "conversation_id": "my-app-user-1",
  "reason": "morning",
  "private_context": "用户昨天说今天有考试",
  "topic_hint": "轻轻问候一下"
}
```

响应示例：

```json
{
  "message": "醒了吗，今天是不是要考试来着",
  "life": {
    "date": "2026-05-22",
    "time_slot": "上午",
    "current_activity": "刚醒一会儿",
    "recent_meal": "喝了水",
    "mood": "普通",
    "availability": "available",
    "topic_seed": "问问今天安排",
    "next_update_at": "2026-05-22 11:30",
    "reply_delay_seconds": 0,
    "reply_delay_reason": "",
    "day_plan_summary": "...",
    "recent_timeline_summary": "..."
  },
  "relationship_memory": "...",
  "trace_id": "...",
  "policy": {
    "should_reply": true,
    "reply_mode": "calm",
    "user_state": "normal",
    "risk_level": "low",
    "reason": "按真人历史风格自然短回。",
    "reply_delay_seconds": 0,
    "reply_delay_reason": ""
  },
  "silenced": false
}
```

`private_context` 是内部触发背景，不会作为用户消息写入历史。生成的 AI 消息会在传入
`conversation_id` 时写入 live memory。

## 记忆接口

### `GET /memory/stats`

查看向量库和回写队列状态。

**响应字段：**

| 字段 | 类型 | 说明 |
|---|---|---|
| `friend_messages` | int | 单条对方消息 chunk 总数 |
| `dialogue_windows` | int | 多轮对话窗口 chunk 总数 |
| `response_pairs` | int | 用户输入→对方回复样本对总数 |
| `live_messages` | int | 运行时回写的消息数（含 user_new + ai_generated） |
| `relationship_memories` | int | 关系记忆条数（用户近况蒸馏） |
| `writeback_enabled` | bool | `.env` 里 `WRITEBACK_ENABLED` 的值 |
| `writeback_paused` | bool | 是否被 `/memory/writeback/pause` 暂停 |

### `POST /memory/search`

调试用检索接口，只跑检索，不调用聊天模型。前端诊断面板可以用它看"到底召回了什么"。

**请求体：**

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `query` | string | 是 | — | 查询文本；空字符串返回空结果 |
| `conversation_id` | string \| null | 否 | `null` | 用于限定 `recent_live` 范围 |
| `top_k` | int | 否 | `12` | 融合结果上限 |

**响应字段：**

| 字段 | 类型 | 说明 |
|---|---|---|
| `fused` | MemorySearchHit[] | 最终融合后的结果，最接近主 prompt 实际用的记忆 |
| `response_pairs` | MemorySearchHit[] | 用户输入→对方回复样本召回 |
| `friend_examples` | MemorySearchHit[] | 单条对方消息召回 |
| `dialogue_windows` | MemorySearchHit[] | 多轮上下文片段召回 |
| `recent_live` | MemorySearchHit[] | 当前会话最近 live 记忆 |
| `trace_id` | string | 本次请求追踪 ID |

每个 `MemorySearchHit` 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `chunk_id` | string | 唯一 ID，可用于后续 `DELETE /memory/{table}/{id}` |
| `kind` | `friend` / `window` / `live` / `response_pair` | chunk 种类 |
| `text` | string | chunk 文本 |
| `score` | float | 排序分数；越大越相关 |
| `rank` | int | 在对应 list 内的排名（1 起） |
| `timestamp_ms` | int | 原始消息时间，Unix 毫秒 |
| `session_id` | string | 同一段连续对话的标识 |
| `sender_name` | string | 发送方名字 |
| `source` | enum | 见下方说明 |
| `warmth` | float | 暖度得分（亲密关系词频） |

`source` 取值与语义：

- `human_original` / `history`：真人原始聊天，**允许**作为 persona / 风格证据
- `user_new`：新会话用户输入，用于事实记忆和"用户最近发生了什么"，**不参与风格蒸馏**
- `ai_generated`：AI 分身回复，用于连续性检索；默认只在同一 `conversation_id` 内参与语义检索。开启 `AI_GENERATED_LONG_TERM_ENABLED=true` 后才会跨会话长期检索

### `POST /memory/writeback/pause`

暂停 live memory 回写。**响应：** `{"status": "paused"}`

### `POST /memory/writeback/resume`

恢复 live memory 回写。**响应：** `{"status": "running"}`

### `DELETE /memory/{table}/{memory_id}`

软删除某条记忆（标记 `deleted`，检索和统计跳过）。

**Path 参数：**

| 字段 | 说明 |
|---|---|
| `table` | 允许 `friend_messages` / `live_messages` / `response_pairs`；其它返回 `400` |
| `memory_id` | `MemorySearchHit.chunk_id` 拿到的 ID |

**响应：** `{"status": "deleted"}`

**错误：** `400`（表名非法）/ `404`（id 找不到）

## 元信息接口

### `GET /healthz`

基础进程健康检查。**唯一不需要鉴权**的端点，可用于容器存活探针。

**响应字段：**

| 字段 | 类型 | 说明 |
|---|---|---|
| `status` | `"ok"` | 进程能响应 HTTP 即返回 ok |
| `version` | string | 当前后端版本号 |

### `GET /readyz`

检查必要配置、向量库连接和 persona 卡片是否就绪。用于反代/编排的就绪探针。
`ready=false` 时仍返回 `200`（不是 503），方便编排器读 issues 决定下一步。

**响应字段：**

| 字段 | 类型 | 说明 |
|---|---|---|
| `ready` | bool | 全部检查通过才是 `true` |
| `issues` | string[] | 不就绪的具体原因（每条人类可读，如 `"缺少 persona_card.md"`） |

### `GET /info` 和 `GET /v1/info`

返回前端需要的应用名、模型名、关系类型和 persona 卡片状态。两个路径完全等价。

**响应字段：**

| 字段 | 类型 | 说明 |
|---|---|---|
| `app_name` | string | 前端 title 显示用 |
| `app_slogan` | string | 副标题文案 |
| `friend_name` | string | 对方名字（被模仿者） |
| `self_name` | string | 用户名字 |
| `relationship_type` | `friend` / `lover` / `family` / `colleague` / `custom` | 关系类型 |
| `relationship_description` | string | 人类可读的关系描述 |
| `persona_template` | string | 内置模板名或自定义模板路径 |
| `embedding_model` | string | 向量模型名 |
| `chat_model` | string | 主聊天模型名（请求里的 `model` 字段会被忽略，实际就用这个） |
| `version` | string | 后端版本号 |
| `has_persona_card` | bool | persona_card.md 是否已生成 |

## 文档提取接口

把上传的文档转成纯文本，让前端拼到 user message 里发出去。LLM 完全不感知文件概念。

### `GET /v1/documents/formats`

列出当前后端支持的扩展名。

| 字段 | 类型 | 说明 |
|---|---|---|
| `extensions` | string[] | 小写扩展名（无前导点） |

### `POST /v1/documents/extract`

上传文档并提取纯文本。请求格式是 **multipart/form-data**，不是 JSON。

**Form 字段：**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file` | file | 是 | 待提取的文件，扩展名必须在 `/formats` 返回列表里 |

**响应字段：**

| 字段 | 类型 | 说明 |
|---|---|---|
| `filename` | string | 上传时的原文件名 |
| `extension` | string | 小写扩展名（不带点） |
| `text` | string | 提取出来的文本 |
| `char_count` | int | `text` 的字符数 |
| `estimated_tokens` | int | 粗略 token 估算（≈ 字符数 / 4），仅供前端预算用 |

**错误：** `400`（缺文件名 / 文件为空 / 扩展名不支持 / 解析失败）

```bash
curl -F "file=@notes.pdf" \
  -H "Authorization: Bearer $XUWEN_API_KEY" \
  http://127.0.0.1:8000/v1/documents/extract
```

## 表情包与图片接口

表情包用于 AI 真正"发表情"的能力。后端把可用表情列表注入到 prompt，AI 决定要发时输出
`[sticker:名字]`，前端识别并渲染对应图片。字节文件持久化在 `STICKER_DATA_DIR`，元数据存 LanceDB。

`Owner` 枚举：

- `ai`：只让 AI 发，用户不可发
- `self`：仅用户面板可见
- `shared`：双方都能用，默认值

### `GET /v1/stickers`

列出表情包。

**Query 参数：**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `owner` | `ai` / `self` / `shared` | 否 | 留空返回全部，传值则按 owner 过滤 |

**响应：** `{"items": [StickerResponse, ...]}`

每条 `StickerResponse` 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | 唯一标识，AI 用 `[sticker:name]` 指代 |
| `description` | string | 给 AI 看的语义描述（决定何时使用） |
| `owner` | Owner | 见上方枚举 |
| `tags` | string[] | 软分类标签 |
| `extension` | string | 实际文件扩展（png/jpg/jpeg/gif/webp/bmp） |
| `sha` | string | 图片文件 sha256 |
| `created_at_ms` | int | 入库时间，Unix 毫秒 |
| `image_url` | string | 相对路径，拼到 API 基址后可拉取图片 |

### `POST /v1/stickers`

新建表情包。

**请求体：**

| 字段 | 类型 | 必填 | 默认 | 约束 |
|---|---|---|---|---|
| `name` | string | 是 | — | 1-32 字符；必须唯一，重复返回 `409` |
| `description` | string | 是 | — | 1-200 字符；给 AI 看的语义 |
| `data_url` | string | 是 | — | `data:image/<ext>;base64,...`，受 `STICKER_MAX_IMAGE_BYTES` 限制（默认 2MB） |
| `owner` | Owner | 否 | `shared` | 见枚举 |
| `tags` | string[] | 否 | `[]` | — |

**响应：** `201` + `StickerResponse`

**错误：** `400`（data URL 非法 / 超大 / 扩展不支持）/ `409`（同名已存在）

### `PATCH /v1/stickers/{name}`

更新已有表情包的**元数据**（不能改图片字节，要改图请删了重建）。

**Path：** `name`（要修改的表情名）

**请求体（三个字段都可选）：**

| 字段 | 类型 | 说明 |
|---|---|---|
| `description` | string \| null | 新描述 |
| `owner` | Owner \| null | 改归属 |
| `tags` | string[] \| null | 整体替换标签数组（不是追加） |

**响应：** `200` + 更新后的 `StickerResponse`

**错误：** `400` / `404`（表情包不存在）

### `DELETE /v1/stickers/{name}`

软删除表情包（图片文件保留，仅从可用列表移除）。

**响应：** `{"status": "deleted", "name": "ok"}` / **错误：** `404`

### `GET /v1/stickers/{name}/image`

返回表情包的图片字节。响应 `content-type` 按扩展自动设置，带 `Cache-Control: public, max-age=86400`。

**错误：** `404`

### `GET /images/{sha}`

通过 sha256 读取已持久化的**聊天图片**（用户发来的或多轮里出现的）。

**Path：** `sha`（sha256 十六进制全长）

**响应：** 图片字节，`content-type` 按 magic bytes 推断。

**错误：** `404`（sha 对不上文件）

## 调试接口

仅在 `DEBUG_ENDPOINTS_ENABLED=true` 时挂载。所有 `/debug/*` 端点都过鉴权中间件。

### `GET /debug/stats`

详细运行诊断。

**响应主要字段：**

| 字段 | 类型 | 说明 |
|---|---|---|
| `version` | string | 后端版本号 |
| `memory` | object | 各表 chunk 数量，结构同 `GET /memory/stats` |
| `database` | object | LanceDB 操作耗时分布（`by_operation` + `recent`） |
| `life` | object | 当前 AI 生活状态快照 + 今日计划 + 最近时间线 |
| `writeback` | object | 回写队列统计：`enqueued` / `written` / `flushed_batches` / `dropped` / `failed` / `paused` / `pending_turns` |
| `calls.<stage>` | object | 按 stage 分组的延迟统计：`count` / `error_count` / `error_rate` / `avg/p50/p95_latency_ms` / 最近 N 条记录。常见 stage：`retrieval`、`llm.complete`、`llm.stream`、`life.decide`、`response.policy`、`response.policy.refined`、`responses.complete`、`responses.stream`、`web.search`、`web.fetch`、`chat.silenced`、`companion.silenced` |
| `model_chain` | array | 最近 80 次模型调用的完整链路：`trace_id` / `stage` / `attempt` / `model` / `url` / `stream` / `latency_ms` / `status` / `status_code` / `upstream_request_id` / `request`（messages 摘要） / `response`（content 预览） / `error` |

### `GET /debug/config`

脱敏配置快照。API key 只显示是否已配置，不返回具体值。

**响应主要字段：**

| 字段 | 类型 | 说明 |
|---|---|---|
| `app_name` / `app_slogan` / `app_timezone` | string | 应用元数据 |
| `self_name` / `friend_name` / `relationship_type` / `persona_template` | string | 身份与关系 |
| `chat_model` / `embedding_model` / `embedding_dim` / `embedding_input_mode` / `embedding_batch_size` / `embedding_max_concurrency` / `embedding_max_requests_per_minute` | mixed | 模型配置 |
| `session_gap_minutes` / `window_size` / `window_overlap` / `final_context_k` / `rrf_k` / `recency_half_life_days` | int/float | 切分与检索参数 |
| `writeback_enabled` / `writeback_batch_turns` / `writeback_vectorize` | bool/int | 回写策略 |
| `live_top_k` / `ai_generated_source_weight` / `ai_generated_long_term_enabled` | mixed | live 检索与来源权重 |
| `response_policy` | object | `model_enabled` / `model` / `endpoint_overridden` / `key_overridden` / `temperature` / `max_tokens` —— 是否启用小模型复核及是否单独配置 endpoint |
| `silence_response_sentinel` / `silence_finish_reason` | string | 沉默响应配置 |
| `responses_store_capacity` | int | /v1/responses 服务端 LRU 缓存容量 |
| `vision_enabled` / `chat_model_supports_vision` | bool | 视觉链路开关 |
| `web_access_enabled` / `web_search_provider` / `web_search_base_url_configured` / `web_search_client_active` / `web_fetch_enabled` / `web_fetch_client_active` / `web_fetch_max_urls` / `web_fetch_max_bytes` / `web_fetch_max_chars` | mixed | 联网与 URL 读取 |
| `enable_pii_redaction` | bool | PII 脱敏总开关 |
| `api_keys_configured` | object | `openai` / `embedding` / `vision` / `web_search` / `local_guard` 是否各自已配置（值都是 bool，**不含具体 key**） |
| `paths` | object | `lance_db` / `persona` / `images` 实际落盘路径 |
| `env` | object | `python` 解释器版本 + `process_pid` |

### `POST /debug/metrics/reset`

清空内存里的运行指标，不删除 LanceDB 数据。

**响应：** `{"status": "ok"}`

清空内存里的运行指标，不删除 LanceDB 数据。

## 错误格式

业务错误通常返回：

```json
{
  "error": {
    "code": "xuwen.some_code",
    "message": "可读错误信息",
    "request_id": "..."
  }
}
```

FastAPI 参数校验错误使用标准 `422` 响应。
