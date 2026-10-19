# Backend API

Afterglow is a FastAPI service. Its main integration entry is the OpenAI-compatible `/v1/chat/completions`; other routes provide proactive openings, memory inspection, initialization, stickers, images, document extraction and diagnostics.

Client and upstream protocols are independent. Clients may call `/v1/chat/completions` or `/v1/responses`; `CHAT_API_PROTOCOL` determines whether the backend calls the upstream `/chat/completions` or `/responses` for final text.

Default address: `http://127.0.0.1:8000`.

Application APIs require authentication by default except `/healthz`. Set `XUWEN_API_KEY` in the backend `.env` and supply either `Authorization: Bearer <XUWEN_API_KEY>` or `x-api-key: <XUWEN_API_KEY>`. A configured key is always validated, even with `API_AUTH_REQUIRED=false`. If auth is required but the key is empty, protected routes return `503 xuwen.auth_config`. Disable auth only for isolated local development. The setup wizard is separate and uses the session token printed by its process.

Every response includes `x-request-id`. Chat, memory search and proactive responses also provide `trace_id` for model-chain diagnostics in `/debug/stats`.

Send `Accept-Language: en` or `zh-CN` for localized API messages and generated analysis. Simplified/Traditional Chinese tags are supported, including quality weights. If no supported language is requested, backend `XUWEN_LANGUAGE` is used (default English). Proactive messages always follow the backend language; normal chat follows conversation/explicit language requests. Error codes, enum values, keys, source evidence and persisted user content remain stable. Missing translations have readable fixed fallbacks rather than raw keys.

```text
http://127.0.0.1:8000
```

```http
Authorization: Bearer <XUWEN_API_KEY>
x-api-key: <XUWEN_API_KEY>
```

## Core endpoints



### `POST /v1/chat/completions`

This is the recommended entry for third-party chat integrations. In addition to a normal Chat Completions call, Afterglow retrieves LanceDB memories and adds persona, relationship memory, actual current time, Life state, optional search and URL context. It supports images when vision is enabled. A supplied `conversation_id` enables complete-turn live-memory writeback.

**Model selection:** `model` is accepted as a compatibility placeholder and ignored. Operators choose `CHAT_MODEL` in `.env`; the response reports the actual model used.

**Policy extension:** The top-level nonstandard `policy` object includes `should_reply`, `reply_mode`, `user_state`, `risk_level`, `reason`, `reply_delay_seconds` and `reply_delay_reason`. Display delay is a client responsibility; the backend does not block to simulate it. The first SSE chunk also contains `policy`. SDKs that ignore extra fields can still consume standard fields.

When policy chooses silence, the response uses `finish_reason="silenced"`, `content="[silent]"` (configurable through `SILENCE_RESPONSE_SENTINEL`) and `policy.should_reply=false`. Strict SDK finish-reason enums can use `SILENCE_FINISH_REASON=stop` instead.

**Schedule tasks:** `schedule_tasks` is `list[ScheduleTask] | null`, returned only when `SCHEDULE_EXTRACT_ENABLED=true` and a scheduling intent was extracted. Task fields:

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Short random ID, e.g. `t_a1b2c3`, for client deduplication |
| `trigger_at` | string | Absolute first trigger, timezone-aware ISO 8601, e.g. `2026-06-01T07:00:00+08:00` |
| `recurrence` | string or null | iCalendar RRULE subset, e.g. `FREQ=DAILY;BYHOUR=7;BYMINUTE=0`; null for one-time |
| `message` | string | Text to deliver at trigger time |
| `title` | string | Optional short title |
| `source` | string | `extractor` by default; `main` is reserved and currently unused |

The main model emits hidden `<schedule-hint>Wake me tomorrow at 7</schedule-hint>` intent. The schedule model normalizes it into a task. Failure, timeout or disabled extraction yields null without disrupting the reply. Empty `SCHEDULE_*` uses Label, Policy, Life, then main-model fallback. Afterglow returns tasks; the client/scheduler implements delivery.

**Burst interruption and merging:** Optional `caller_id` is a stable caller/session ID. Only requests sharing that caller cancel each other; separate callers can proceed concurrently. `client_message_id` identifies a user bubble for idempotent queuing. Without it, temporary IDs weaken retry deduplication. Older clients omitting these fields retain ordinary behavior.

Send each user bubble immediately rather than adding a one-second debounce. A new request for the same caller cancels the previous generation and merges its still-unanswered bubbles with the new text into `current_user_text`, separated by double newlines. Successfully replied IDs are acknowledged and removed. Supply only completed history plus the newest bubble in `messages`; including unanswered old bubbles there would duplicate them with the pending queue.

Cancelled requests do not write live/relationship memory or acknowledge pending bubbles. Streaming requests finish as soon as practical with `finish_reason="cancelled"`. Non-streaming remote inference may continue, but the backend checks generation freshness before returning and gives empty content with `cancelled` when superseded.

**Multiple bubbles:** A single assistant message uses double newlines (`\n\n`) to express separate bubbles, without multiple choices/calls. Single newlines remain within a bubble for lists/code/poetry. Clients wanting IM-style presentation split the complete content on double newlines and optionally stagger bubbles (the original integration recommendation is 1.5–3 seconds). Check the active conversation before each delayed insertion; see `finishAssistantMessage` in `frontend/src/stores/chat.ts`. Clients wanting one bubble can render the content as ordinary Markdown paragraphs.

For SSE, double newlines may span deltas: concatenate the stream before splitting, not each delta separately. The initial `policy.reply_delay_seconds` applies to the whole response, separately from inter-bubble delays.

**Life events:** With `LIFE_MARKER_UPDATE_ENABLED=true`, the model may append a hidden `<life-event>Going to eat; busy briefly</life-event>`. The backend strips it and asynchronously asks the Life model to normalize/update state. Legacy `<life-update>` is accepted as input only, not applied as a direct state patch. Streaming filters buffer incomplete marker blocks. Disabling the switch still strips markers to keep the internal protocol hidden.

**Images:** Use standard multimodal text/image_url content. Enable `VISION_ENABLED=true`; set `CHAT_MODEL_SUPPORTS_VISION=true` for native main-model vision, otherwise configure `VISION_API_URL`, `VISION_API_KEY` and `VISION_MODEL`.

**Web access:** With `WEB_ACCESS_ENABLED=true`, explicit requests for current public information may invoke Tavily/SearXNG. Supplied HTTP(S) links may be read when `WEB_FETCH_ENABLED=true`. Bare domains are not fetched unconditionally: local intent rules followed by the `LIFE_*` model confirm access. Fetching is text-only, without JavaScript, and rejects local/private/link-local addresses while limiting redirects, bytes and extracted text. Diagnostics use `calls.web.search`, `web.search.skipped`, `web.intent`, `web.fetch` and `web.fetch.skipped`.

Examples below show a request, non-streaming response, streaming curl and multimodal input.

```json
{
  "model": "gpt-4o-mini",
  "messages": [
    {"role": "user", "content": "Are you there?"}
  ],
  "stream": false,
  "temperature": 0.7,
  "max_tokens": 300,
  "conversation_id": "my-app-user-1",
  "caller_id": "my-app-user-1",
  "client_message_id": "u-20260623-0001"
}
```

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1779400000,
  "model": "gpt-4o-mini",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "I am here. What is up?"},
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
    "reason": "A brief natural reply following historical style.",
    "reply_delay_seconds": 0,
    "reply_delay_reason": ""
  }
}
```

```bash
curl -H "Accept-Language: en" -N http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $XUWEN_API_KEY" \
  -d '{
    "messages": [{"role": "user", "content": "I am a bit tired today"}],
    "stream": true,
    "conversation_id": "demo"
  }'
```

```json
{
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "Look at this image"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
      ]
    }
  ]
}
```

### `POST /v1/responses`

An OpenAI Responses-compatible subset sharing retrieval, policy, Life state, relationship memory and silence behavior with Chat Completions.

Supported fields:

- `model`: accepted and ignored; backend `CHAT_MODEL` wins.
- `input`: string or message array; `input_image` uses the chat vision chain.
- `instructions`: converted to a system message at the top of the prompt.
- `stream`: standard-style Responses events.
- `temperature`, `top_p`, `max_output_tokens`.
- `previous_response_id`: in-process LRU lookup reuses the prior conversation; explicit `conversation_id` takes precedence.
- `conversation_id`: Afterglow writeback extension.
- `store`: ignored semantically; the bounded process LRU is always used and is lost on restart.

Unsupported: `tools`, function calling, file inputs, `image_generation`, `code_interpreter`, MCP and `background` mode.

Non-streaming fields: `id` (`resp_xxx`), `object="response"`, `created_at`, `model`, `status="completed"`, `output[]` containing message items with `content[]` of `output_text`, convenience `output_text`, `usage`, `trace_id`, `policy` and `previous_response_id`.

Streaming emits created → in_progress → output_item.added → content_part.added → repeated output_text.delta → output_text.done → content_part.done → output_item.done → completed → `[DONE]`.

Silence traverses the full event sequence, with one delta containing the configured sentinel and completed status. Detect `policy.should_reply=false` or a matching sentinel. Multiple bubbles use the same double-newline rules as chat: accumulate deltas before splitting, then optionally stagger 1.5–3 seconds. The initial policy delay is separate.

For chaining, send the previous response's ID. After a process restart the cache is gone; use explicit stable conversation IDs for continuity.

Examples: request and streaming event order.

```bash
curl -H "Accept-Language: en" http://127.0.0.1:8000/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $XUWEN_API_KEY" \
  -d '{
    "instructions": "Chat with me in the familiar tone",
    "input": "I am a bit tired today",
    "conversation_id": "demo-1"
  }'
```

```
event: response.created → event: response.in_progress
→ event: response.output_item.added → event: response.content_part.added
→ event: response.output_text.delta (repeated) → event: response.output_text.done
→ event: response.content_part.done → event: response.output_item.done
→ event: response.completed → data: [DONE]
```

### `POST /v1/companion/proactive` (experimental, not recommended)

Generate an immediate opening. Quality, topic selection and disturbance prevention are unstable; this interface is for local experiments rather than production. It bypasses learned scheduling gates. Proactive text always follows `XUWEN_LANGUAGE`, including hooks, rewrites and fixed fallbacks.

`caller_id` optionally selects recent caller context; a stable `conversation_id` alone is also usable. `private_context` is internal trigger context, not stored as a user message. Generated AI text is written to live memory when a conversation ID is supplied and to the recent context cache.

Topic hooks prioritize recent cache, relationship memory, then RAG snippets. `.data/persona/proactive_context_cache.json` defaults to 40 short messages per caller/conversation and injects at most 12. It is short-term continuity data, not long-term style evidence.

Examples: request with `reason`, `private_context`, `topic_hint`; response with message, Life state, relationship memory, trace, policy and `silenced`.

```json
{
  "conversation_id": "my-app-user-1",
  "caller_id": "my-im-caller-1",
  "reason": "morning",
  "private_context": "The user said yesterday that they have an exam today",
  "topic_hint": "A gentle greeting"
}
```

```json
{
  "message": "Are you up? Was your exam today?",
  "life": {
    "date": "2026-05-22",
    "time_slot": "Morning",
    "current_activity": "Just woke up",
    "recent_meal": "Had some water",
    "mood": "Neutral",
    "availability": "available",
    "topic_seed": "Ask about today plans",
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
    "reason": "A brief natural reply following historical style.",
    "reply_delay_seconds": 0,
    "reply_delay_reason": ""
  },
  "silenced": false
}
```

### `POST /v1/companion/proactive/poll` (experimental, not recommended)

Polling maintains a pending candidate per conversation/caller and returns the next request time. External clients/schedulers/bots perform polling and delivery.

1. First call usually returns `state="scheduled"` and `next_poll_at_ms`.
2. Wait until that timestamp before polling again.
3. If no intervening user activity and current score/gates allow it, `state="sent"` includes the opening at `proactive.message`.
4. Chat/Responses user activity is automatically recorded and cancels pending candidates. Only systems bypassing those routes need to pass `last_user_message_at_ms` themselves.

When due and allowed, `auto_send=true` returns `sent` or `silenced`; `proactive` has the immediate-generation response structure. Examples below include the complete scheduling timestamps, cancellation flag, score, features and nullable generated result.

```json
{
  "conversation_id": "my-app-user-1",
  "caller_id": "my-im-caller-1",
  "reason": "learned_rhythm",
  "auto_send": true
}
```

```json
{
  "state": "scheduled",
  "should_send": false,
  "score": 0.0,
  "threshold": 0.55,
  "reason": "scheduled",
  "skip_reasons": [],
  "features": {},
  "private_context": "",
  "topic_hint": "",
  "profile_summary": "",
  "next_poll_at_ms": 1783180800000,
  "scheduled_for_ms": 1783180800000,
  "candidate_created_at_ms": 1783177200000,
  "cancelled_by_user_activity": false,
  "proactive": null
}
```

### `POST /v1/companion/proactive/decide` (experimental, not recommended)

Compute an explainable send/skip decision from learned openings, without maintaining a pending candidate. Useful for experiments and scheduler debugging, not recommended for production.

Requires explicit `PROACTIVE_ENABLED=true`. Runtime prefers valid independent `proactive_analysis.json`, then import-time `.data/persona/proactive_profile.json`, then reconstructs from `dialogue_windows`. The presence of a fallback profile does not establish that data is suitable for automatic messaging; run independent analysis and quality checks first.

If `auto_send=true` and gates pass, `proactive` contains the immediate-generation result. Otherwise the response reports `should_send`, score/threshold, reason, skip reasons, weighted features, private context, topic hint, profile summary and `next_check_seconds`.

```json
{
  "conversation_id": "my-app-user-1",
  "caller_id": "my-im-caller-1",
  "reason": "learned_rhythm",
  "auto_send": false
}
```

```json
{
  "should_send": true,
  "score": 0.76,
  "threshold": 0.55,
  "reason": "matched_learned_rhythm",
  "skip_reasons": [],
  "features": {
    "time_match": 0.9,
    "idle_gap_match": 0.8,
    "availability": 1.0
  },
  "private_context": "Proactive candidate reason: learned_rhythm...",
  "topic_hint": "Following historical opening habits...",
  "profile_summary": "Identified 12 friend-initiated openings...",
  "next_check_seconds": 900,
  "proactive": null
}
```

### `GET /v1/companion/proactive/profile` (experimental diagnostics)

Inspect the learned profile and recent audit records: candidate time, sent/skipped status, score, skip reasons and feature weights. Full audit is persisted to `.data/persona/proactive_audit.jsonl`.



## Memory endpoints



### `GET /memory/stats`

Vector table and writeback state:

| Field | Type | Meaning |
|---|---|---|
| `friend_messages` | int | Standalone friend-message chunks |
| `dialogue_windows` | int | Multi-turn historical windows |
| `response_pairs` | int | User-input → friend-reply examples |
| `history_images` | int | Historical image-description chunks |
| `live_messages` | int | Runtime user_new + ai_generated messages |
| `relationship_memories` | int | Distilled recent user facts |
| `writeback_enabled` | bool | Configured `WRITEBACK_ENABLED` |
| `writeback_paused` | bool | Paused through the writeback API |



### `POST /memory/search`

Retrieval-only diagnostics; does not call the main chat model.

| Request field | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `query` | string | Yes | — | Query; empty string returns empty results |
| `conversation_id` | string or null | No | `null` | Scope for `recent_live` |
| `top_k` | int | No | `12` | Fused result limit |

| Response field | Type | Meaning |
|---|---|---|
| `fused` | MemorySearchHit[] | Fused results closest to actual prompt memory |
| `response_pairs` | MemorySearchHit[] | Input/reply examples |
| `friend_examples` | MemorySearchHit[] | Standalone friend messages |
| `dialogue_windows` | MemorySearchHit[] | Multi-turn contexts |
| `history_images` | MemorySearchHit[] | Image descriptions |
| `recent_live` | MemorySearchHit[] | Recent current-conversation memory |
| `trace_id` | string | Request trace |

Each `MemorySearchHit`:

| Field | Type | Meaning |
|---|---|---|
| `chunk_id` | string | Unique ID usable for deletion |
| `kind` | enum | `friend`, `window`, `history_image`, `live`, `response_pair` |
| `text` | string | Chunk text |
| `score` | float | Larger means more relevant |
| `rank` | int | One-based rank within its list |
| `timestamp_ms` | int | Original Unix time in milliseconds |
| `session_id` | string | Continuous-conversation identifier |
| `sender_name` | string | Sender display name |
| `source` | enum | Source/trust category below |
| `warmth` | float | Relationship-vocabulary warmth score |

Source meanings:

- `human_original` / `history`: human history, eligible as persona/style evidence.
- `user_new`: current-user facts and recent activities; excluded from style distillation.
- `ai_generated`: AI continuity material; semantic retrieval stays within the same conversation unless `AI_GENERATED_LONG_TERM_ENABLED=true` permits low-weight cross-conversation recall.



### `POST /memory/writeback/pause`

Pause live-memory writeback. Response: `{"status": "paused"}`.



### `POST /memory/writeback/resume`

Resume live-memory writeback. Response: `{"status": "running"}`.



### `DELETE /memory/{table}/{memory_id}`

Soft-delete a memory (`deleted` flag); retrieval and counts skip it.

| Path field | Meaning |
|---|---|
| `table` | Only `friend_messages`, `live_messages`, `response_pairs`; other values return 400 |
| `memory_id` | `MemorySearchHit.chunk_id` |

Response: `{"status": "deleted"}`. Errors: 400 for invalid table, 404 for missing ID.



## Metadata endpoints



### `GET /healthz`

Basic process liveness; the only application API endpoint exempt from authentication by default. Suitable for container liveness probes.

| Field | Type | Meaning |
|---|---|---|
| `status` | `"ok"` | Process can respond over HTTP |
| `version` | string | Backend version |



### `GET /readyz`

Checks required configuration, database connectivity and persona-card readiness. Returns HTTP 200 even when `ready=false`; orchestrators should inspect the JSON rather than only the status code.

| Field | Type | Meaning |
|---|---|---|
| `ready` | bool | True only when all checks pass |
| `issues` | string[] | Human-readable reasons for not being ready |



### `GET /info` and `GET /v1/info`

Equivalent routes for frontend metadata:

| Field | Type | Meaning |
|---|---|---|
| `app_name` | string | Application title |
| `app_slogan` | string | Subtitle |
| `friend_name` | string | Target person's display name |
| `self_name` | string | User display name |
| `relationship_type` | enum | `friend`, `lover`, `family`, `colleague`, `custom` |
| `relationship_description` | string | Readable relationship description |
| `persona_template` | string | Built-in template name or custom path |
| `embedding_model` | string | Embedding model |
| `chat_model` | string | Actual backend-selected model; client `model` is ignored |
| `version` | string | Backend version |
| `has_persona_card` | bool | Whether persona_card.md exists |



## Document extraction endpoints

Convert uploads into plain text for clients to include in a user message. The chat model receives text, not a file object.



### `GET /v1/documents/formats`

List supported extensions. Response `extensions: string[]` contains lowercase names without a leading dot.



### `POST /v1/documents/extract`

Send **multipart/form-data**, not JSON. Required form field `file` contains a document with an extension returned by `/formats`.

| Response field | Type | Meaning |
|---|---|---|
| `filename` | string | Original upload filename |
| `extension` | string | Lowercase extension without dot |
| `text` | string | Extracted text |
| `char_count` | int | Text character count |
| `estimated_tokens` | int | Rough characters/4 estimate for client budgeting |

Errors: 400 for missing filename, empty upload, unsupported extension or parse failure. Example:

```bash
curl -H "Accept-Language: en" -F "file=@notes.pdf" \
  -H "Authorization: Bearer $XUWEN_API_KEY" \
  http://127.0.0.1:8000/v1/documents/extract
```

## Sticker and image endpoints

The backend lists available stickers in the prompt. The model selects one with `[sticker:name]`; clients render its image. Bytes live under `STICKER_DATA_DIR` and metadata in LanceDB.

`Owner` enum:

- `ai`: AI only.
- `self`: only visible in the user's picker.
- `shared`: both, default.



### `GET /v1/stickers`

Optional query `owner` filters `ai|self|shared`; omit to return all. Response: `{"items": [StickerResponse, ...]}`.

Each `StickerResponse`:

| Field | Type | Meaning |
|---|---|---|
| `name` | string | Unique identifier used by `[sticker:name]` |
| `description` | string | Semantic guidance for choosing it |
| `owner` | Owner | Ownership enum above |
| `tags` | string[] | Soft category labels |
| `extension` | string | png/jpg/jpeg/gif/webp/bmp |
| `sha` | string | Image SHA-256 |
| `created_at_ms` | int | Stored time in Unix milliseconds |
| `image_url` | string | Relative path to append to the API base |



### `POST /v1/stickers`

Create a sticker:

| Field | Type | Required | Default | Constraint |
|---|---|---|---|---|
| `name` | string | Yes | — | Unique, 1–32 characters; duplicate returns 409 |
| `description` | string | Yes | — | 1–200 characters; guidance for AI |
| `data_url` | string | Yes | — | `data:image/<ext>;base64,...`, limited by `STICKER_MAX_IMAGE_BYTES` (2 MiB default) |
| `owner` | Owner | No | `shared` | Ownership enum |
| `tags` | string[] | No | `[]` | Labels |

Response: 201 + StickerResponse. Errors: 400 for invalid/oversized/unsupported image data; 409 for duplicate name.



### `PATCH /v1/stickers/{name}`

Update metadata only; changing bytes requires deleting/recreating. Path `name` identifies the sticker. All body fields are optional:

| Field | Type | Meaning |
|---|---|---|
| `description` | string or null | New description |
| `owner` | Owner or null | New ownership |
| `tags` | string[] or null | Replace the entire array, not append |

Response: 200 + updated StickerResponse. Errors: 400 or 404 (missing sticker).



### `DELETE /v1/stickers/{name}`

Soft-delete a sticker from available choices; image bytes remain. Response: `{"status": "deleted", "name": "ok"}` for a sticker named `ok`. Error: 404.



### `GET /v1/stickers/{name}/image`

Return image bytes with content type determined by extension and `Cache-Control: public, max-age=86400`. Error: 404.



### `GET /images/{sha}`

Read a persisted chat image by its full SHA-256 hexadecimal hash. Returns bytes with content type inferred from magic bytes. Error: 404 when no matching file exists.



## Relationship analysis API

Analysis parses original chat files with per-message timestamps instead of reconstructing evidence from LanceDB. All routes use main API authentication. `Accept-Language` selects generated report language; existing stored reports are returned as stored rather than translated on read.



### `POST /analysis/start`

Start a background task. `files` accepts filenames or absolute paths inside `CONFIG_UI_UPLOADS_DIR`; empty selects all `.json`/`.jsonl` there. For other locations use `xuwen analyze <paths...>`.

Response includes `task_id`, `status`, `progress`, `stage` and the eventual `report`.

Network, authentication and model-interface errors cancel outstanding requests and fail the task, without Reduce or publishing reports. Successful block caches remain; default `resume=true` fills missing work on retry. Generated cache reuse requires matching output language; legacy caches are recomputed.

Policy refusals/repeated invalid JSON skip only that block, without a final-model fallback or failing the whole analysis. It is cached as empty and excluded from final candidates. `.data/analysis/failures/<block_id>.json` records block text, parse errors and raw outputs; the task and manifest count skipped blocks. Use `--list-blocks` or `--inspect-block <id>` to inspect without model calls.

```json
{
  "files": ["chat-export.json"],
  "timeline": true,
  "personality": true,
  "resume": true,
  "since": "2023-01"
}
```

### Task status

| Method and path | Purpose |
|---|---|
| `GET /analysis/{task_id}` | Task status |
| `GET /analysis/{task_id}/stream` | SSE progress; closes when terminal |
| `POST /analysis/{task_id}/cancel` | Cancel a running task |



### Read-only results

| Method and path | Purpose |
|---|---|
| `GET /analysis/timeline` | `.data/analysis/timeline.json` |
| `GET /analysis/personality` | Structured personality report |
| `GET /analysis/proactive` | Independent proactive report: both people's openings, AI motive/time attribution, evidence/confidence and target initiative/time distributions |
| `GET /analysis/experimental` | Isolated experimental results; 404 when disabled |

Experimental output is excluded from ordinary timeline/personality responses. Block models extract candidates; the final model reorganizes personality, relationship stages, experimental profiles and Life patterns. `personality_prompt_context.md` is evidence-free guidance; Life data goes into `life_profile.json` and rendered `life_context.md`. Main-prompt use requires `ANALYSIS_PERSONALITY_PROMPT_ENABLED`; Life use requires `ANALYSIS_LIFE_CONTEXT_ENABLED`.

Life extraction has an independent block Map: a short block only supplies timestamped sleep/meal/activity/availability candidates, not proof of long-term habits. The final model judges stability across blocks and references `source_ids`; code restores original evidence by ID. Its independent cache version permits rerunning Life extraction while reusing relationship/experimental caches.

`life_context.md` is a long-term semantic prior combined with timestamp-derived circadian data, actual time, today's plan and observed state changes. It changes candidate probabilities, not facts about the present. Current conversation/today's state take priority.

Each observation has `subject`: `friend`, `self`, `both`, `relationship` or `unknown`. Regular personality accepts target observations; relationship sections may accept friend/both/relationship; Life accepts friend only. The model explicitly returns `category`, `time_patterns`, `contexts`, `target_fields` and `sensitive_relationship_context`; code does not infer them by scanning Chinese keywords. User habits, joint activities, unknown subjects and sensitive relationship content are excluded from the Life timeline.

`ANALYSIS_EXPERIMENTAL_ENABLED` adds a separate experimental Map to avoid competing for ordinary report tokens. Final merging writes `experimental/personality_profile.json` and an evidence-bearing Markdown report. Only the additional `ANALYSIS_EXPERIMENTAL_PROMPT_ENABLED` switch creates evidence-free `experimental/prompt_context.md`. These are uncertain hypotheses about personality, interpersonal attitudes, attachment, possible concealment, manipulation and mental health, not a reply-strategy checklist. Preserve confidence, conditions, counterexamples and alternatives.

Relationship analysis is outside the setup wizard; use `/analysis/*` or CLI. See [Advanced features](advanced.md#chat-analysis).



### What is sent to the main chat model

Each chat request includes one system message, recent turns and the current user message. System context contains persona/style statistics, Life/relationship state, retrieved human replies/windows, output rules and optional regular/experimental personality summaries. Web results are included when used.

Full structured personality/Life reports, timeline, full experimental report, evidence and session IDs are not wholesale injected. `personality_prompt_context.md` goes to the main model with its switch; `life_context.md` goes to the Life model with its switch; experimental context requires both experimental switches. Current conversation and retrieved human evidence have higher factual priority. Analysis itself still sends selected source blocks to the Analysis API as a separate necessary operation.



## Debug endpoints

Mounted only with `DEBUG_ENDPOINTS_ENABLED=true`. All `/debug/*` routes are authenticated.



### `GET /debug/stats`

Detailed runtime diagnostics:

| Field | Type | Meaning |
|---|---|---|
| `version` | string | Backend version |
| `memory` | object | Table counts, same as memory stats |
| `database` | object | LanceDB timings: `by_operation`, `recent` |
| `life` | object | Current state, today's plan and recent timeline |
| `writeback` | object | `enqueued`, `written`, `flushed_batches`, `dropped`, `failed`, `paused`, `pending_turns` |
| `calls.<stage>` | object | Counts, errors/rate, average/p50/p95 latency and recent records per stage; e.g. retrieval, llm.complete/stream, life.decide, response.policy/refined, responses.complete/stream, web.search/fetch, chat.silenced, companion.silenced |
| `model_chain` | array | Recent 80 calls: trace_id, stage, attempt, model, URL, stream, latency, status/status_code, upstream_request_id, request message summaries, response preview and error |



### `GET /debug/config`

Redacted configuration snapshot. API keys expose only configured/not-configured booleans, never the key value.

| Field | Type | Meaning |
|---|---|---|
| `app_name` / `app_slogan` / `app_timezone` | string | Application metadata |
| `self_name` / `friend_name` / `relationship_type` / `persona_template` | string | Identity/relationship |
| `chat_model` / `embedding_model` / `embedding_dim` / `embedding_input_mode` / `embedding_batch_size` / `embedding_max_concurrency` / `embedding_max_requests_per_minute` | mixed | Model configuration |
| `session_gap_minutes` / `window_size` / `window_overlap` / `final_context_k` / `rrf_k` / `recency_half_life_days` | int/float | Splitting/retrieval settings |
| `writeback_enabled` / `writeback_batch_turns` / `writeback_vectorize` | bool/int | Writeback policy |
| `live_top_k` / `ai_generated_source_weight` / `ai_generated_long_term_enabled` | mixed | Live retrieval and trust |
| `response_policy` | object | model_enabled, model, endpoint_overridden, key_overridden, temperature, max_tokens |
| `analysis` | object | Analysis model, experimental generation/context switches |
| `silence_response_sentinel` / `silence_finish_reason` | string | Silence configuration |
| `responses_store_capacity` | int | Responses LRU capacity |
| `vision_enabled` / `chat_model_supports_vision` | bool | Vision switches |
| `web_access_enabled` / `web_search_provider` / `web_search_base_url_configured` / `web_search_client_active` / `web_fetch_enabled` / `web_fetch_client_active` / `web_fetch_max_urls` / `web_fetch_max_bytes` / `web_fetch_max_chars` | mixed | Search and URL fetching |
| `enable_pii_redaction` | bool | PII redaction switch |
| `api_keys_configured` | object | openai, embedding, vision, web_search, local_guard booleans, without values |
| `paths` | object | lance_db, persona, images actual paths |
| `env` | object | Python version and process_pid |



### `POST /debug/proactive/force-due` (experimental diagnostics)

Local debugging only: set a conversation/caller pending candidate's `scheduled_for_ms` to now so the next poll reaches due-time checks. Requires debug endpoints; do not call from production paths. Request and response examples:

```json
{
  "conversation_id": "proactive-full-123",
  "caller_id": "my-im-caller-1"
}
```

```json
{
  "forced": true,
  "conversation_id": "proactive-full-123",
  "previous_scheduled_for_ms": 1783180800000,
  "scheduled_for_ms": 1783177200000,
  "candidate_created_at_ms": 1783177000000
}
```

### `POST /debug/metrics/reset`

Clear in-memory metrics without deleting LanceDB data. Response: `{"status": "ok"}`.



## Error format

Business errors generally return an `error` object with stable `code`, readable `message` and `request_id`. Messages follow request language with the backend default as fallback; raw upstream diagnostics and filenames may retain their original language. Validation failures use HTTP 422. Example:

```json
{
  "error": {
    "code": "xuwen.some_code",
    "message": "Readable error message",
    "request_id": "..."
  }
}
```

