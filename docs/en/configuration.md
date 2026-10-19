# Configuration

Configuration lives in `backend/.env`. The complete field template is `backend/.env.example`. Restart the backend fully after changes: `uvicorn --reload` does not reload cached Settings when `.env` changes.

This page covers first-time decisions. See [Environment variables](environment.md) for every supported setting, default and dependency.

## Required configuration

| Category | Fields | Purpose |
|---|---|---|
| You | `SELF_NAME`, `SELF_UID` | Your display name and one or more platform UIDs |
| Target person | `FRIEND_NAME`, `FRIEND_UID` | Person whose style is modeled and their UIDs |
| Chat model | `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `CHAT_MODEL`, `CHAT_API_PROTOCOL` | OpenAI-compatible Chat Completions or Responses upstream |
| Embeddings | `EMBEDDING_API_URL`, `EMBEDDING_API_KEY`, `EMBEDDING_MODEL`, `EMBEDDING_DIM` | Vectorizing history and queries |
| API authentication | `XUWEN_API_KEY` | Bearer token clients use to access Afterglow |

Separate multiple accounts belonging to the same person with commas:

```env
SELF_UID=u_qq_me,wxid_me
FRIEND_UID=u_qq_friend,wxid_friend
```

Never combine different people in `FRIEND_UID`.

## Model roles

| Role | Required | Purpose | Settings |
|---|---|---|---|
| Main chat model | Yes | Final replies | `OPENAI_*`, `CHAT_MODEL`, `CHAT_API_PROTOCOL` |
| Embeddings | Yes | History and query vectors | `EMBEDDING_*` |
| Labeling | No | mood/topic/importance labels | `LABELING_ENABLED`, `LABEL_*` |
| Life state | No | Life timeline and web-access intent | `LIFE_*` |
| Response policy | No | Bounded refinement after rule-based decisions | `RESPONSE_POLICY_*` |
| Reranker | No | Reorder retrieved candidates | `CROSS_RERANK_*` |
| Vision | No | Historical image descriptions | `VISION_*` |
| Web search | No | Public current information | `WEB_ACCESS_ENABLED`, `WEB_SEARCH_*` |

No provider is mandatory. Check protocol support, dimensions, concurrency limits, retention and training policies before sending real chats.

The English wizard fills empty services with Google chat and OpenAI embeddings/auxiliary models; Chinese-only presets are hidden in English. These UI suggestions do not overwrite saved values or change backend defaults. Include the provider's full base path: OpenAI uses `https://api.openai.com/v1`, Google uses `https://generativelanguage.googleapis.com/v1beta/openai`. Afterglow appends business endpoints; it does not add `/v1` automatically. Do not include `/chat/completions`, `/responses` or `/embeddings` in the base URL.

Backend output language is `XUWEN_LANGUAGE=en|zh-CN`, default `en`. The wizard's relationship step saves this independently from the page language. Restart after changing it. CLI `--language` and HTTP `Accept-Language` override their own output scope; proactive messages always follow the backend setting.

## Embedding rate limits

```env
EMBEDDING_BATCH_SIZE=25
EMBEDDING_MAX_CONCURRENCY=20
EMBEDDING_MAX_REQUESTS_PER_MINUTE=0
```

For 429 errors, reduce concurrency and set a requests-per-minute cap matching your provider. Changing the embedding model or dimension requires rebuilding the affected vector tables; old vectors cannot be reused.

## API authentication

A nonempty `XUWEN_API_KEY` always enables token validation. Setting `API_AUTH_REQUIRED=false` does not disable an already configured key; fully disabling authentication also requires clearing it. Keep authentication enabled outside isolated local development.

The setup token printed at startup is separate from `XUWEN_API_KEY`. Use the session token in `/config/`, and the API key in chat clients.

## Do AI replies become long-term memory?

By default, `AI_GENERATED_LONG_TERM_ENABLED=false`: long-term imitation evidence comes from human history; AI replies do not become cross-conversation material. With `true`, AI replies participate in long-term retrieval at low weight, and the companion may develop habits that differ from the original person. Read [Responsible use and privacy](privacy.md) before enabling it.

## Fully offline operation

Point chat, embeddings and every enabled optional model to local compatible services such as Ollama or vLLM. Optional chains include `LABEL_*`, `LIFE_*`, `VISION_*`, `RESPONSE_POLICY_*`, `QUERY_REWRITE_*`, `RERANK_*`, `CROSS_RERANK_*`, `ADAPTIVE_CHUNK_*`, `ANALYSIS_*`, `ANALYSIS_FINAL_*` and `SCHEDULE_*`. Disable unused features. Also disable:

```env
WEB_ACCESS_ENABLED=false
WEB_FETCH_ENABLED=false
UPDATE_CHECK_ENABLED=false
```

Chat text stays off external providers only when all models and tools used are local. Update checks are a separate outbound request and do not contain chat text.
