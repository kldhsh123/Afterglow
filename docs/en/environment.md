# Backend configuration and environment variables

Afterglow uses `pydantic-settings` to read process environment variables and `.env` in the backend working directory. This reference covers the supported Settings fields; the copyable template is [`backend/.env.example`](https://github.com/kldhsh123/Afterglow/blob/main/backend/.env.example).

> [!IMPORTANT]
> `.env` contains model keys, access passwords and identities. Never commit it. Fully restart after changes; `uvicorn --reload` does not rebuild cached Settings for `.env` edits.

Use the [configuration editor](config-editor.md) to generate a file locally in your browser. Templates are available with [English comments](https://github.com/kldhsh123/Afterglow/blob/main/backend/.env.example) or [Chinese comments](https://github.com/kldhsh123/Afterglow/blob/main/backend/.env.example.zh-CN); their values are identical.

## Loading rules

- Process environment variables override `.env`; field names are case-insensitive and documented in uppercase.
- `.env` and relative paths use the process working directory. Source instructions start with `cd backend`.
- Unknown fields are ignored, so typos may not fail startup. Check this reference and `.env.example`.
- “Default/example” below primarily reflects the template, not every code fallback. `CHAT_MODEL` is `gpt-4o` in the template and `gpt-4o-mini` without `.env`; `LABEL_MAX_CONCURRENCY` is `19` versus `1`.
- The configuration UI's field whitelist also comes from `.env.example`; update both when adding Settings fields.

These are backend/template defaults, not English wizard suggestions. The English wizard fills only unconfigured services with Google chat and OpenAI embeddings/auxiliary models, preserving saved values. See [Configuration](configuration.md) and [privacy](privacy.md).

## Important dependencies

- `RELATIONSHIP_TYPE=custom` requires `RELATIONSHIP_DESCRIPTION`.
- `SELF_UID`/`FRIEND_UID` accept comma-separated accounts belonging to the same person; do not merge different people.
- Empty `LIFE_*` falls back to the main model; `RESPONSE_POLICY_*` falls back through Life.
- Empty `QUERY_REWRITE_*`/`SCHEDULE_*` prefers Label, then Policy/Life/main.
- Empty `RERANK_*` uses Policy/Life/main; adaptive chunking prefers Label, then Life/main.
- `VISION_ENABLED=true` with `CHAT_MODEL_SUPPORTS_VISION=false` requires independent vision URL/key/model. Historical-image import always needs its independent VLM chain.
- `WEB_FETCH_ENABLED` is subordinate to `WEB_ACCESS_ENABLED`.
- `WINDOW_OVERLAP` must be less than `WINDOW_SIZE`; adaptive maximum characters must be at least the target.
- Changing embedding model/dimension requires rebuilding existing vector tables/indexes.
- `WRITEBACK_VECTORIZE=false` stores zero vectors; those Live messages do not take part in semantic recall.

## Application, identity and relationship

| Variable | Default/example | Purpose |
|---|---|---|
| `XUWEN_LANGUAGE` | `en` | Backend default and proactive-message language: `en` or `zh-CN`. CLI `--language` overrides command output; ordinary HTTP uses `Accept-Language` when supported. Proactive output always follows this setting. Restart after changes. |
| `APP_NAME` | `Afterglow` | Application name shown by the API and frontend. |
| `APP_SLOGAN` | `把曾经对你好的话，续成往后的陪伴` | Application subtitle; the existing configured/default text is preserved, not auto-translated. |
| `APP_TIMEZONE` | `Asia/Shanghai` | IANA time zone supplied to models; affects real time, circadian patterns and schedules. |
| `SELF_NAME` | Empty | Your display name; required identity setting. |
| `SELF_UID` | Empty | Your platform UID; comma-separated accounts are supported. |
| `FRIEND_NAME` | Empty | Target person's display name; required identity setting. |
| `FRIEND_UID` | Empty | Target person's platform UID; comma-separated accounts are supported. |
| `SELF_UIDS` | Empty | Legacy multiple-UID list; prefer comma-separated `SELF_UID`. |
| `FRIEND_UIDS` | Empty | Legacy friend UID list; prefer `FRIEND_UID`. |
| `SELF_ALIASES` | Empty | Your real name, usernames, nicknames and aliases, comma-separated. |
| `FRIEND_ALIASES` | Empty | Target person's aliases for cleaning, retrieval and prompt identity matching. |
| `RELATIONSHIP_TYPE` | `friend` | One of `friend`, `lover`, `family`, `colleague`, `custom`. |
| `RELATIONSHIP_DESCRIPTION` | Empty | Free-form relationship description; required for `custom`, optionally overrides defaults otherwise. |
| `PERSONA_TEMPLATE` | `xuwen` | Built-in `xuwen/friend/lover/family/colleague` or an absolute custom `.md.j2` path. |
| `PROMPT_TEMPLATE_DIR` | Empty | Override the built-in template directory; empty means unset. |

## Main chat model

| Variable | Default/example | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | Empty | Main chat model API key. |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible base URL; include the required provider path (such as `/v1`), without `/chat/completions` or `/responses`. |
| `CHAT_MODEL` | `gpt-4o` | Final reply model; code fallback without `.env` is `gpt-4o-mini`. |
| `CHAT_API_PROTOCOL` | `chat_completions` | Main upstream protocol: `chat_completions` or `responses`; auxiliary models are unaffected. |
| `CHAT_TIMEOUT_SECONDS` | `60` | Main chat request timeout in seconds; greater than zero. |
| `LLM_REASONING_EFFORT` | `null` | Reasoning effort for compatible LLMs: `none`, `low`, `medium`, `high`, `xhigh`; `null` omits the field. Applies to chat, Life, policy, schedules, rewrite, adaptive chunking, labels and VLM; not embeddings/search/rerank. |

Responses upstream sends `store=false`. Prompt caching is automatic only where supported and depends on provider, input length and exact prefix matching; protocol switching does not guarantee savings. Inspect `model_chain[].response.usage.cached_tokens` for reported hits. Life, policy, rewrite and LLM rerank still use Chat Completions.

## Life-state model

| Variable | Default/example | Purpose |
|---|---|---|
| `LIFE_API_URL` | Empty | Life-state model base URL; empty reuses the main model. |
| `LIFE_API_KEY` | Empty | Life-state API key; empty reuses the main model key. |
| `LIFE_MODEL` | Empty | Model maintaining `life_state.json`; empty uses `CHAT_MODEL`. |
| `LIFE_TEMPERATURE` | `0.35` | State JSON generation temperature, `0..2`. |
| `LIFE_MAX_TOKENS` | `1500` | Maximum state JSON output tokens; allow room for reasoning on thinking models. |
| `LIFE_TIMEOUT_SECONDS` | `30` | Life-state request timeout in seconds; greater than zero. |
| `LIFE_UPDATE_INTERVAL_MINUTES` | `60` | Maximum interval before state reevaluation; `0` uses events and `next_update_at` only. |
| `LIFE_MAX_REPLY_DELAY_SECONDS` | `45` | Maximum suggested reply presentation delay. |
| `LIFE_SLEEPING_MIN_REPLY_DELAY_SECONDS` | `8` | Minimum fallback delay while `sleeping`; `0` disables it. |
| `LIFE_MARKER_UPDATE_ENABLED` | `true` | Normalize and strip main-model `<life-event>` asynchronously; legacy `<life-update>` input is accepted. `false` still strips markers. |

## Response policy model

| Variable | Default/example | Purpose |
|---|---|---|
| `RESPONSE_POLICY_MODEL_ENABLED` | `false` | Enable bounded small-model refinement after rule-based policy. |
| `RESPONSE_POLICY_API_URL` | Empty | Policy model URL; empty reuses Life/main chain. |
| `RESPONSE_POLICY_API_KEY` | Empty | Policy API key; empty reuses Life/main chain. |
| `RESPONSE_POLICY_MODEL` | Empty | Policy model name; empty reuses Life/main chain. |
| `RESPONSE_POLICY_TEMPERATURE` | `0.2` | Policy output temperature. |
| `RESPONSE_POLICY_MAX_TOKENS` | `260` | Maximum policy JSON output tokens. |
| `RESPONSE_POLICY_TIMEOUT_SECONDS` | `8` | Policy timeout in seconds; greater than zero. |

## Output, silence and Responses API

| Variable | Default/example | Purpose |
|---|---|---|
| `SILENCE_RESPONSE_SENTINEL` | `[silent]` | Response `content` marker when the policy chooses silence. |
| `SILENCE_FINISH_REASON` | `silenced` | `silenced` or `stop`; use `stop` for strict OpenAI SDK enums. |
| `AI_SILENCE_ENABLED` | `true` | Allow model-chosen silence in low-risk situations; does not disable rule-based safety silence. |
| `RESPONSE_STREAMING_ENABLED` | `false` | `false` sends complete text over simulated SSE; `true` enables real streaming. |
| `RESPONSES_STORE_CAPACITY` | `512` | In-process LRU capacity mapping `previous_response_id` to `conversation_id`; lost on restart. |

### Long-conversation summaries

`/v1/chat/completions` and `/v1/responses` incrementally summarize older history when either threshold below is exceeded, retaining recent messages and the current input verbatim. Summaries use the `LIFE_*` model configuration and run alongside RAG, relationship memory and Life-state preparation. Initial or subsequent compression can add one or more model calls; later turns reuse the cache to reduce main-model input. Character counts are estimates, not model token counts.

| Variable | Default/example | Purpose |
|---|---|---|
| `CONVERSATION_SUMMARY_ENABLED` | `true` | Enable long-conversation summaries; disabling sends the full history. |
| `CONVERSATION_SUMMARY_TRIGGER_CHARS` | `16000` | Update when the combined character count of unsummarized history and existing summary exceeds this value. |
| `CONVERSATION_SUMMARY_TRIGGER_MESSAGES` | `40` | Update when unsummarized historical messages exceed this count. |
| `CONVERSATION_SUMMARY_KEEP_MESSAGES` | `16` | Maximum recent original messages retained after compression. |
| `CONVERSATION_SUMMARY_RECENT_CHARS` | `8000` | Recent-message character budget; keeps whole messages and tries to preserve question/answer pairs. |
| `CONVERSATION_SUMMARY_MAX_CHARS` | `3000` | Maximum summary characters. |
| `CONVERSATION_SUMMARY_BATCH_CHARS` | `12000` | New source characters per summary call; very long messages are processed in batches. |
| `CONVERSATION_SUMMARY_TIMEOUT_SECONDS` | `30` | Total timeout budget for a summary update; must be finite and positive. |

Summaries are cached in `PERSONA_DATA_DIR/conversation_summaries/` under hashed conversation IDs. A stable `conversation_id` (or `caller_id` fallback for Chat Completions) allows reuse across requests and restarts; without one, compression applies only to the supplied request. The cache verifies the original history prefix and rebuilds after edits or branching. Summaries provide conversation continuity: they do not rewrite original chats or become historical or long-term relationship facts. Failure stops the current request and preserves the previous cache. Existing Live writeback and `relationship_memory.md` continue independently.

The main prompt uses the final fused/reranked candidates; `FINAL_CONTEXT_K` controls their total. Built-in templates no longer repeat recent original messages in the system prompt; custom templates can still use `recent_conversation`.

Simulated Chat Completions SSE sends the complete reply before parsing schedules, then includes `schedule_tasks` in the closing chunk. This avoids delaying visible text without requiring token streaming. A single `stream=false` JSON response still waits for schedule extraction.

After all uploaded files have been stored, the configuration wizard attempts vector-index creation using `LANCE_INDEX_MIN_ROWS`. Failure produces a warning and preserves imported data; the task report lists index results for each table. Use `uv run xuwen index --language en` to create indexes for existing data or `uv run xuwen optimize --language en` to organize newly added data.

## Proactive messages (experimental)

| Variable | Default/example | Purpose |
|---|---|---|
| `PROACTIVE_ENABLED` | `false` | Runtime proactive-message switch; currently not recommended for production. |
| `PROACTIVE_LEARNING_MIN_GAP_MINUTES` | `120` | Minimum silence gap used to recognize a new conversation opening. |
| `PROACTIVE_SCORE_THRESHOLD` | `0.55` | Minimum proactive trigger score. |
| `PROACTIVE_CHECK_INTERVAL_SECONDS` | `900` | Suggested polling interval for clients/schedulers. |
| `PROACTIVE_MIN_IDLE_MINUTES` | `180` | Minimum current-conversation idle time. |
| `PROACTIVE_MAX_PER_DAY` | `1` | Maximum proactive messages per conversation/day; `0` prohibits sending. |
| `PROACTIVE_QUIET_HOURS` | `00:00-08:30` | Quiet interval, including overnight ranges; empty disables it. |
| `PROACTIVE_SKIP_WHEN_LIFE_BUSY` | `true` | Skip initiation when Life state is busy, sleeping or away. |
| `PROACTIVE_PROFILE_WINDOW_LIMIT` | `10000` | Recent dialogue-window limit for profile fallback reconstruction. |
| `PROACTIVE_AUDIT_MAX_RECORDS` | `200` | In-memory proactive audit record window. |
| `PROACTIVE_CONTEXT_CACHE_ENABLED` | `true` | Keep short recent context per caller/conversation. |
| `PROACTIVE_CONTEXT_CACHE_MAX_ITEMS` | `40` | Maximum cached messages per conversation. |
| `PROACTIVE_CONTEXT_CACHE_PROMPT_ITEMS` | `12` | Maximum cached messages injected into proactive generation. |
| `PROACTIVE_CONTEXT_CACHE_MAX_TEXT_CHARS` | `240` | Maximum characters per cached message. |

## Embeddings

| Variable | Default/example | Purpose |
|---|---|---|
| `EMBEDDING_API_URL` | `https://maas-api.cn-huabei-1.xf-yun.com/v2` | OpenAI-compatible embedding base URL; include provider path, without `/embeddings`. |
| `EMBEDDING_API_KEY` | Empty | Embedding service key. |
| `EMBEDDING_MODEL` | `xop3qwen8bembedding` | Embedding model name. |
| `EMBEDDING_DIM` | `4096` | Model output and LanceDB schema dimension; positive and identical. Rebuild existing vectors after changes; see [Troubleshooting](troubleshooting.md#import-fails-with-spill-has-sent-an-error). |
| `EMBEDDING_TIMEOUT_SECONDS` | `60` | Embedding timeout in seconds; greater than zero. |
| `EMBEDDING_INPUT_MODE` | `array` | `array` for standard batches, `single` for one input at a time. |
| `EMBEDDING_BATCH_SIZE` | `25` | Maximum texts per request; forced to `1` in `single` mode. |
| `EMBEDDING_MAX_CONCURRENCY` | `20` | Maximum in-flight HTTP requests; greater than zero. |
| `EMBEDDING_MAX_REQUESTS_PER_MINUTE` | `0` | Request cap per minute; `0` means no client-side cap. |
| `EMBEDDING_INCLUDE_ENCODING_FORMAT` | `true` | Send `encoding_format=float`; disable for incompatible gateways. |
| `EMBEDDING_SEND_DIMENSIONS` | `true` | Send `dimensions`; usually needed with MRL models. |
| `EMBEDDING_RETRY_ATTEMPTS` | `8` | Total attempts including the first request. |
| `EMBEDDING_RETRY_MAX_WAIT_SECONDS` | `10` | Maximum single exponential-backoff delay. |
| `EMBEDDING_MAX_CONSECUTIVE_FAILURES` | `3` | Consecutive exhausted requests before import treats failure as systemic and stops. Isolated failures skip their batch; rerunning fills gaps. |

## Vision and historical images

| Variable | Default/example | Purpose |
|---|---|---|
| `VISION_ENABLED` | `false` | Enable vision, including offline historical-image description. |
| `CHAT_MODEL_SUPPORTS_VISION` | `false` | Whether the main model accepts OpenAI multimodal image content directly. |
| `VISION_API_URL` | Empty | Independent VLM base URL when the main model lacks vision; always needed for historical-image import. |
| `VISION_API_KEY` | Empty | Independent VLM key. |
| `VISION_MODEL` | `qwen-vl-plus` | Independent VLM model. |
| `VISION_DESCRIBE_PROMPT` | `Describe the image objectively in one or two sentences. Do not invent details.` | Image-to-text instruction; a custom prompt is preserved as supplied. |
| `VISION_TIMEOUT_SECONDS` | `15` | Online chat/responses VLM description budget in seconds; positive. Does not change VisionClient's default 60-second HTTP timeout. |
| `VISION_MAX_IMAGE_BYTES` | `8388608` | Maximum bytes per image; default 8 MiB. |
| `IMAGE_DATA_DIR` | `.data/images` | Directory for original images stored by SHA-256. |

## Web search and page fetching

| Variable | Default/example | Purpose |
|---|---|---|
| `WEB_ACCESS_ENABLED` | `false` | Master web switch; false disables search and URL fetching. |
| `WEB_SEARCH_PROVIDER` | `tavily` | `tavily` or `searxng`. |
| `WEB_SEARCH_BASE_URL` | `https://api.tavily.com` | Search service base URL. |
| `WEB_SEARCH_API_KEY` | Empty | Search key; often unnecessary for SearXNG. |
| `WEB_SEARCH_MAX_RESULTS` | `5` | Requested result count; capped at `10` by code. |
| `WEB_SEARCH_TIMEOUT_SECONDS` | `8` | Search timeout in seconds; nonnegative. |
| `WEB_SEARCH_LANGUAGE` | `zh-CN` | Search language hint; independent of backend output language. |
| `WEB_FETCH_ENABLED` | `true` | Fetch user-supplied public URLs; still requires web master switch. |
| `WEB_FETCH_TIMEOUT_SECONDS` | `8` | Per-page fetch timeout in seconds. |
| `WEB_FETCH_MAX_URLS` | `2` | Maximum fetched URLs per turn; capped at `5`. |
| `WEB_FETCH_MAX_REDIRECTS` | `3` | Maximum redirects; capped at `10`. |
| `WEB_FETCH_MAX_BYTES` | `524288` | Maximum page download size; capped at 2 MiB. |
| `WEB_FETCH_MAX_CHARS` | `6000` | Maximum page characters injected into the prompt; capped at `20000`. |

## Storage, authentication, configuration UI and privacy

| Variable | Default/example | Purpose |
|---|---|---|
| `LANCE_DB_PATH` | `.data/lancedb` | LanceDB directory. |
| `LANCE_UPSERT_BATCH_SIZE` | `128` | Rows per `merge_insert` batch; smaller reduces memory at a throughput cost. Does not fix vector dimension mismatches. |
| `LANCE_INDEX_MIN_ROWS` | `1000` | Rows required before automatic physical indexing; `0` disables it. |
| `PERSONA_DATA_DIR` | `.data/persona` | Persona, circadian, Life state and proactive profile directory. |
| `ANALYSIS_DATA_DIR` | `.data/analysis` | Timeline, personality, block cache and isolated experimental output directory. |
| `API_AUTH_REQUIRED` | `true` | Require API authentication; `/healthz` remains public. |
| `XUWEN_API_KEY` | Empty | Bearer/`x-api-key` access password; any nonempty value always enables validation. |
| `CONFIG_UI_ENABLED` | `false` | Explicitly mount the wizard; missing critical settings may also enable it automatically. |
| `CONFIG_UI_PATH_PREFIX` | `/config` | Wizard path; must begin with `/` and avoid overlapping business API prefixes. |
| `CONFIG_UI_LOCALHOST_ONLY` | `true` | Restrict the configuration UI to local clients. |
| `CONFIG_UI_SETUP_TOKEN` | Empty | Separate setup token; empty generates a random session token at startup. |
| `CONFIG_UI_UPLOADS_DIR` | `.data/uploads` | Temporary directory for wizard uploads. |
| `ENABLE_PII_REDACTION` | `true` | Redact phone numbers, emails, identity documents, bank cards and IPs before storage. |
| `PII_RULES_PATH` | Empty | Custom YAML/JSON redaction rules; empty means unset. |

### API authentication matrix

| `API_AUTH_REQUIRED` | `XUWEN_API_KEY` | Result |
|---|---|---|
| `false` | Empty | Open API access |
| `false` | Nonempty | Token still required |
| `true` | Empty | Business routes return `503` |
| `true` | Nonempty | Token required normally |

## Chat splitting

| Variable | Default/example | Purpose |
|---|---|---|
| `CHUNKING_STRATEGY` | `fixed` | `fixed` windows or `adaptive` topic-based splitting. |
| `SESSION_GAP_MINUTES` | `30` | Start a new session when adjacent messages exceed this gap. |
| `WINDOW_SIZE` | `12` | Messages per fixed multi-turn window. |
| `WINDOW_OVERLAP` | `3` | Overlap between windows; must be strictly less than `WINDOW_SIZE`. |
| `SINGLE_CONTEXT_BEFORE` | `6` | Context messages before a standalone friend message. |
| `SINGLE_CONTEXT_AFTER` | `2` | Context messages after a standalone friend message. |
| `SINGLE_CONTEXT_MAX_CHARS` | `600` | Adaptive-mode character budget for a message's embedding context. |
| `ADAPTIVE_CHUNK_MODEL_ENABLED` | `false` | Use a small model for adaptive topic boundaries. |
| `ADAPTIVE_CHUNK_API_URL` | Empty | Chunking model URL; empty follows the fallback chain. |
| `ADAPTIVE_CHUNK_API_KEY` | Empty | Chunking model key. |
| `ADAPTIVE_CHUNK_MODEL` | Empty | Chunking model name. |
| `ADAPTIVE_CHUNK_TEMPERATURE` | `0` | Boundary output temperature, `0..2`. |
| `ADAPTIVE_CHUNK_MAX_TOKENS` | `900` | Maximum boundary JSON output tokens. |
| `ADAPTIVE_CHUNK_TIMEOUT_SECONDS` | `60` | Adaptive chunking timeout in seconds; positive. |
| `ADAPTIVE_CHUNK_MAX_MESSAGES_PER_CALL` | `80` | Maximum messages per boundary-model call. |
| `ADAPTIVE_CHUNK_TARGET_CHARS` | `700` | Target chunk characters. |
| `ADAPTIVE_CHUNK_MAX_CHARS` | `1400` | Hard character limit; at least `ADAPTIVE_CHUNK_TARGET_CHARS`. |
| `ADAPTIVE_CHUNK_MIN_TURNS` | `3` | Minimum turns per adaptive chunk. |
| `ADAPTIVE_CHUNK_OVERLAP_TURNS` | `2` | Overlap turns between adaptive chunks. |
| `ADAPTIVE_CHUNK_SOFT_GAP_MINUTES` | `8` | Soft time gap used to assist boundaries. |
| `ADAPTIVE_CHUNK_MAX_CONCURRENCY` | `0` | Positive explicitly sets concurrency; `0` uses Label concurrency or falls back to `4`. |
| `ADAPTIVE_CHUNK_CACHE_PATH` | `.data/ingestion/adaptive_chunk_cache.jsonl` | Model boundary cache; resumed/repeated imports avoid calls on hits. Remove this file to reset it. |

## Relationship analysis

Analysis runs only when explicitly requested with `xuwen analyze` or the task API. Empty model fields fall back to chat. It can send all selected cleaned chats to the configured provider; review [privacy](privacy.md).

| Variable | Default/example | Purpose |
|---|---|---|
| `ANALYSIS_API_URL` | Empty | OpenAI-compatible block-analysis URL; empty uses the main chat model. |
| `ANALYSIS_API_KEY` | Empty | Analysis key; empty uses the main chat key. |
| `ANALYSIS_MODEL` | Empty | Analysis model; empty uses `CHAT_MODEL`. |
| `ANALYSIS_FINAL_API_URL` | Empty | Final relationship-stage, personality, Life and experimental merge URL; empty uses `OPENAI_BASE_URL`. |
| `ANALYSIS_FINAL_API_KEY` | Empty | Final model key; empty uses `OPENAI_API_KEY`. |
| `ANALYSIS_FINAL_MODEL` | Empty | Final report/merge model, not a block-failure fallback; empty uses `CHAT_MODEL`. |
| `ANALYSIS_TEMPERATURE` | `0.3` | Block Map and monthly candidate-merge temperature, `0..2`. |
| `ANALYSIS_MAX_TOKENS` | `6000` | Block/monthly structured-output token cap; too low can truncate JSON. |
| `ANALYSIS_FINAL_MAX_TOKENS` | `6000` | Final model merge/personality output token cap. |
| `ANALYSIS_TIMEOUT_SECONDS` | `120` | Per-request analysis timeout in seconds; positive. |
| `ANALYSIS_BLOCK_CHAR_BUDGET` | `10000` | Character budget per analysis block. |
| `ANALYSIS_MAX_CONCURRENCY` | `3` | Maximum in-flight block-analysis calls. |
| `ANALYSIS_REQUEST_INTERVAL_SECONDS` | `0` | Spacing between request starts; `0` adds no delay. |
| `ANALYSIS_PROACTIVE_ENABLED` | `true` | During explicit analyze-proactive only, use the final model to explain opening motives/timing. Up to 12 openings per batch, with prior tail, opening and first response; stable IDs permit attribution reuse. |
| `ANALYSIS_PROACTIVE_BATCH_SIZE` | `4` | Openings per attribution batch, `1..12`; smaller reduces JSON truncation risk. |
| `ANALYSIS_PROACTIVE_MAX_CONCURRENCY` | `4` | Concurrent proactive-attribution batches; at least `1`. |
| `ANALYSIS_LIFE_CONTEXT_ENABLED` | `false` | Inject inferred sleep/meal/activity/availability patterns into Life decisions; real current time and today's state take precedence. |
| `ANALYSIS_PERSONALITY_PROMPT_ENABLED` | `false` | Provide evidence-free regular personality context to the main chat model; independent of experimental switches. |
| `ANALYSIS_EXPERIMENTAL_ENABLED` | `false` | Add a separate experimental Map per block and a global merge; disabled by default. |
| `ANALYSIS_EXPERIMENTAL_PROMPT_ENABLED` | `false` | Generate an evidence-free, non-diagnostic summary for the main chat model; requires the experimental-generation switch too. |

## Hybrid retrieval

| Variable | Default/example | Purpose |
|---|---|---|
| `RESPONSE_PAIR_TOP_K` | `24` | Response-pair vector recall count. |
| `FRIEND_TOP_K` | `32` | Standalone friend-message recall count. |
| `WINDOW_TOP_K` | `16` | Historical multi-turn window recall count. |
| `HISTORY_IMAGE_TOP_K` | `8` | Historical VLM image-description recall count. |
| `LIVE_TOP_K` | `12` | Live semantic recall count. |
| `FINAL_CONTEXT_K` | `12` | Maximum fused/reranked candidates injected into the main prompt. |
| `RRF_K` | `60` | Reciprocal Rank Fusion smoothing constant. |
| `RETRIEVAL_TIMEOUT_SECONDS` | `15` | Total retrieval budget, including rewrite, embeddings, recall and rerank; positive seconds. |
| `RETRIEVAL_OVERFETCH` | `3` | Per-path overfetch multiplier to compensate for filtering and deduplication. |
| `RECENCY_HALF_LIFE_DAYS` | `30` | Time-decay half-life in days. |
| `RECENCY_MAX_BOOST` | `0.15` | Maximum recency bonus. |
| `WARMTH_BOOST` | `0.12` | Warmth-score weighting coefficient. |
| `LIVE_SOURCE_WEIGHT` | `1.08` | Live source weight. |
| `HISTORY_SOURCE_WEIGHT` | `1.0` | Human text-history weight. |
| `HISTORY_IMAGE_SOURCE_WEIGHT` | `0.75` | Historical image-description fusion weight. |
| `AI_GENERATED_SOURCE_WEIGHT` | `0.25` | Trust weight for AI-generated live memory. |
| `AI_GENERATED_LONG_TERM_ENABLED` | `false` | Allow AI replies in cross-conversation long-term retrieval. |

## Query Rewrite

| Variable | Default/example | Purpose |
|---|---|---|
| `QUERY_REWRITE_ENABLED` | `false` | Rewrite short colloquial messages into retrieval-friendly queries. |
| `QUERY_REWRITE_API_URL` | Empty | Rewrite URL; empty prefers the Label chain. |
| `QUERY_REWRITE_API_KEY` | Empty | Rewrite model key. |
| `QUERY_REWRITE_MODEL` | Empty | Rewrite model name. |
| `QUERY_REWRITE_TEMPERATURE` | `0` | Rewrite temperature, `0..2`. |
| `QUERY_REWRITE_MAX_TOKENS` | `240` | Maximum rewrite output tokens. |
| `QUERY_REWRITE_MAX_VARIANTS` | `3` | Maximum variants including the original query. |
| `QUERY_REWRITE_TIMEOUT_SECONDS` | `30` | Rewrite timeout in seconds; positive. |

## LLM Rerank (experimental)

| Variable | Default/example | Purpose |
|---|---|---|
| `RERANK_ENABLED` | `false` | LLM semantic/style ranking of RRF candidates; usually unsuitable for weak small models. |
| `RERANK_MODE` | `auto` | One of `auto`, `always`, `never`. |
| `RERANK_API_URL` | Empty | LLM reranker URL; empty reuses Policy/Life/main model. |
| `RERANK_API_KEY` | Empty | LLM reranker key. |
| `RERANK_MODEL` | Empty | LLM reranker model. |
| `RERANK_TEMPERATURE` | `0` | Ranking temperature, `0..2`. |
| `RERANK_MAX_TOKENS` | `1200` | Maximum ranking JSON output tokens. |
| `RERANK_TOP_K` | `28` | Candidates supplied to LLM ranking. |
| `RERANK_MIN_CANDIDATES` | `16` | Minimum candidates before ranking runs. |
| `RERANK_TIMEOUT_SECONDS` | `30` | Ranking timeout; failures retain RRF order (fail open). |
| `RERANK_WEIGHT` | `0.55` | Mixing weight between LLM rank and original score, `0..1`. |
| `RERANK_MAX_SAME_SESSION` | `3` | Maximum retained candidates from one session. |

## Cross-encoder Rerank

| Variable | Default/example | Purpose |
|---|---|---|
| `CROSS_RERANK_ENABLED` | `false` | Use a dedicated cross-encoder before optional LLM ranking. |
| `CROSS_RERANK_PROTOCOL` | `jina` | `jina` or `dashscope` request format. |
| `CROSS_RERANK_API_URL` | Empty | Rerank service base URL. |
| `CROSS_RERANK_API_KEY` | Empty | Rerank key. |
| `CROSS_RERANK_MODEL` | Empty | Dedicated reranker name. |
| `CROSS_RERANK_INPUT_K` | `48` | Candidates passed from RRF to the cross-encoder. |
| `CROSS_RERANK_TOP_N` | `16` | Retained candidates; normally at least `FINAL_CONTEXT_K`. |
| `CROSS_RERANK_TIMEOUT_SECONDS` | `5` | Cross-encoder timeout in seconds; positive. |

## Schedule extraction

| Variable | Default/example | Purpose |
|---|---|---|
| `SCHEDULE_EXTRACT_ENABLED` | `false` | Parse hidden `<schedule-hint>` output into structured tasks. |
| `SCHEDULE_API_URL` | Empty | Extractor URL; empty follows Label-to-main fallback chain. |
| `SCHEDULE_API_KEY` | Empty | Extractor key. |
| `SCHEDULE_MODEL` | Empty | Extractor model. |
| `SCHEDULE_TEMPERATURE` | `0.1` | Schedule JSON temperature. |
| `SCHEDULE_MAX_TOKENS` | `400` | Maximum extraction JSON tokens. |
| `SCHEDULE_TIMEOUT_SECONDS` | `60` | Extractor model timeout in seconds; positive. |
| `SCHEDULE_MAX_HINTS_PER_TURN` | `5` | Maximum parsed hints per turn. |
| `SCHEDULE_EXTRACT_TIMEOUT_SECONDS` | `10` | Whole-batch budget; failure returns no tasks without blocking the main reply. |

## Live writeback

| Variable | Default/example | Purpose |
|---|---|---|
| `WRITEBACK_ENABLED` | `true` | Write new conversations to Live Memory. |
| `WRITEBACK_QUEUE_SIZE` | `1000` | Asynchronous writeback queue capacity. |
| `WRITEBACK_BATCH_TURNS` | `8` | Per-conversation turns accumulated before batch embedding/storage. |
| `WRITEBACK_FLUSH_INTERVAL_SECONDS` | `300` | Force flush after this idle interval. |
| `WRITEBACK_VECTORIZE` | `true` | Generate vectors for Live messages; false stores zero vectors excluded from semantic recall. |

## Debugging and metrics

| Variable | Default/example | Purpose |
|---|---|---|
| `DEBUG_ENDPOINTS_ENABLED` | `false` | Expose `/debug/*`; disable after investigation. |
| `DEBUG_MODEL_FULL_PAYLOADS_ENABLED` | `false` | Retain complete prompt/response text in debug records. |
| `METRICS_CAPACITY` | `100` | Ring-buffer capacity for each metric/model-chain record class. |

## Stickers

| Variable | Default/example | Purpose |
|---|---|---|
| `STICKER_DATA_DIR` | `.data/stickers` | Directory for sticker originals and `index.json`. |
| `STICKER_MAX_FOR_AI` | `30` | Maximum available stickers shown to the main model. |
| `STICKER_MAX_IMAGE_BYTES` | `2097152` | Maximum bytes per sticker; default 2 MiB. |
| `STICKER_REJECT_RETRY` | `true` | Retry once when a non-streaming reply selects a nonexistent sticker. |

## Semantic labels

| Variable | Default/example | Purpose |
|---|---|---|
| `LABELING_ENABLED` | `false` | Enable mood/topic/importance soft labels. |
| `LABEL_API_URL` | `https://open.bigmodel.cn/api/paas/v4` | OpenAI-compatible labeling URL. |
| `LABEL_API_KEY` | Empty | Labeling key. |
| `LABEL_MODEL` | `glm-4-flash` | Labeling model name. |
| `LABEL_TIMEOUT_SECONDS` | `30` | Labeling timeout in seconds; positive. |
| `LABEL_BATCH_SIZE` | `8` | Messages per labeling call. |
| `LABEL_MAX_CONCURRENCY` | `19` | Concurrent labeling batches; code fallback without `.env` is `1`. |
| `LABEL_REQUEST_INTERVAL_SECONDS` | `0` | Spacing between request starts; `0` adds no delay. |
| `LABEL_FLUSH_AFTER_N` | `20` | Reserved field; not consumed by the current pipeline. |
| `LABEL_FLUSH_INTERVAL_SECONDS` | `300` | Reserved field; not consumed by the current pipeline. |
| `LABEL_INCREMENTAL` | `true` | Reserved field; offline labeling currently always processes unlabeled chunks only. |
| `LABEL_MAX_CHARS_PER_MESSAGE` | `200` | Maximum characters of each chunk in a labeling prompt. |
| `LABEL_MOOD_VOCAB` | Empty | Comma-separated custom mood vocabulary; empty uses built-in values. |

## Update checks

| Variable | Default/example | Purpose |
|---|---|---|
| `UPDATE_CHECK_ENABLED` | `true` | Query the latest release at startup. |
| `UPDATE_CHECK_URL` | `https://api.github.com/repos/kldhsh123/Afterglow/releases/latest` | Public release lookup URL. |
| `UPDATE_CHECK_TIMEOUT_SECONDS` | `8` | Update-check timeout in seconds. |

