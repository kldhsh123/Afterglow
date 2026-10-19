# Development

This guide is for contributors adding import formats, debugging backend features or preparing pull requests.

## Environment setup

Use Python 3.12.x. Backend:

```bash
cd backend
uv sync --extra dev
cp .env.example .env
```

Frontend:

```bash
cd frontend
pnpm install
```

Start the backend:

```bash
cd backend
XUWEN_LANGUAGE=en uv run uvicorn xuwen.chat_api.app:create_app --factory --reload
```

Start the frontend:

```bash
cd frontend
pnpm dev
```

Set `XUWEN_LANGUAGE=en|zh-CN` for the backend default (English when unset). CLI `--language` overrides command output. HTTP `Accept-Language` controls request-scoped messages and generated analysis. Proactive generation always uses the backend language. Do not translate original evidence, persisted enums or user prompts.

## Backend structure

```text
backend/xuwen/
├── chat_api/      FastAPI, routes, LLM/VLM/web clients and metrics
├── companion/     Life state and relationship memory
├── core/          Shared models, errors, time and metrics
├── ingestion/     Import, cleaning, splitting, embeddings and labeling
├── memory/        LanceDB schemas, IO, retrieval and writeback
└── persona/       Persona analysis, prompt templates and semantic labels
```

Core request flow:

1. `POST /v1/chat/completions` accepts an OpenAI-compatible request.
2. Images are processed according to vision settings.
3. `HybridRetriever` runs five vector recall paths and reads Recent Live for the conversation.
4. `LifeStateManager` reads or updates current life state.
5. `RelationshipMemoryManager` supplies relationship summaries.
6. Optional `WebSearchClient` / `WebFetchClient` adds public web context.
7. `build_chat_messages()` builds the system prompt.
8. `LLMClient` calls the upstream model.
9. If `conversation_id` is supplied, the turn is queued for live-memory writeback.

## Developing import plugins

The pipeline consumes `NormalizedMessage`; it does not need platform-specific knowledge.

Built-in plugins:

| name | display_name | Input | Detection |
|---|---|---|---|
| `afterglow_v1` | Afterglow Chat v1 | Private-chat JSON/JSONL | Metadata or typed/bare JSONL |
| `qqexporter_v5` | QQChatExporter | JSON/JSONL | metadata/chatInfo or JSONL message records |
| `wechat_weflow` | WeChat (WeFlow) | [WeFlow](https://github.com/hicccc77/weflow) JSON/JSONL | `arkme-json` or ChatLab typed records |
| `douyin_chat_export` | Douyin Chat Export | ChatLab JSON/JSONL | `generator=douyin-chat-export`, `platform=douyin` |
| `whatsapp` | WhatsApp | TXT/Markdown | Timestamped lines or WhatsApp Markdown heading |
| `telegram_html` | Telegram Desktop HTML | HTML | history/message HTML structure |
| `discord_chat_exporter` | [DiscordChatExporter](https://github.com/Tyrrrz/DiscordChatExporter) | JSON | guild/channel/exportedAt/messages fields |

Use redacted data when developing or testing WeFlow and other private-chat imports.

The CLI calls registered `match()` methods in order; the first match handles `parse()`. `--plugin <name>` forces selection.

The common loader decodes TXT/Markdown/HTML into a neutral suffix-bearing payload (`TEXT_CONTENT_KEY` / `TEXT_SUFFIX_KEY`) and JSONL into neutral records, without platform-field checks. Plugins use `jsonl_records(payload)` and own all detection/normalization.

The interface lives in `backend/xuwen/ingestion/plugins/__init__.py`:

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


Wizard identity inspection and historical-image references are optional capabilities:

```python
class InspectableImportPlugin(Protocol):
    def inspect(self, payload: dict[str, Any]) -> ImportInspection: ...

class ImageReferenceImportPlugin(Protocol):
    def extract_image_refs(self, payload: dict[str, Any]) -> list[ImportImageRef]: ...
```


Keep format names, identity candidates and image-field interpretation inside the plugin. Do not add platform branches to `parser.py`, the WebUI or `image_importer.py`.

To add a format:

1. Create a module under `backend/xuwen/ingestion/plugins/`, for example `wechat_xxx.py`.
2. Implement `name`, `display_name`, `match()` and `parse()`.
3. Implement `inspect()` for wizard identities and `extract_image_refs()` for historical images if supported.
4. Register it in `backend/xuwen/ingestion/parser.py`.
5. Add tests for auto-detection, forced selection and important message kinds.
6. Run an import against a real, redacted sample.

Minimal example:

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
            if sender_uid in settings.all_self_uids:
                role: SenderRole = "self"
            elif sender_uid in settings.all_friend_uids:
                role = "friend"
            else:
                role = "other"
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


Conventions:

- Keep `match()` lightweight: no file IO or network calls.
- `parse()` must not write the database, call embeddings or mutate global state.
- Individual malformed messages may be skipped; do not swallow whole-format errors.
- `timestamp_ms` is milliseconds.
- Distinguish self/friend correctly; otherwise the user's messages can be treated as the target's style.
- Put non-text media in `placeholders`; retain established markers such as `[图片]` where required by import conventions.

List plugins:

```bash
cd backend
uv run python -m xuwen.ingestion.cli plugins --language en
```

Force a plugin:

```bash
uv run python -m xuwen.ingestion.cli import export.json --plugin qqexporter_v5 --language en
uv run python -m xuwen.ingestion.cli import export.json --plugin wechat_weflow --language en
uv run python -m xuwen.ingestion.cli import export.jsonl --plugin douyin_chat_export --language en
uv run python -m xuwen.ingestion.cli import export.jsonl --plugin afterglow_v1 --language en
```

### Afterglow Chat v1

The intermediate-format specification is in [Afterglow Chat v1](chat-format.md). This page covers plugin development conventions.

## Tests and quality checks

Backend checks:

```bash
cd backend
uv run ruff check xuwen
uv run mypy xuwen
```

Frontend build:

```bash
cd frontend
pnpm build
```

For localization, run `node scripts/check_locales.cjs` from the repository root. Rebuild both frontends after modifying their sources; the config UI's `npm run build` writes tracked files to `backend/xuwen/web_ui/static/`.

## Release version synchronization

Root `VERSION` is the only manually maintained version source. Do not edit Python metadata, lockfiles or Docker examples independently. From the repository root:

```bash
uv run --project backend python scripts/set_version.py 0.4.0
uv run --project backend python scripts/set_version.py --bump patch
uv run --project backend python scripts/set_version.py --bump minor
uv run --project backend python scripts/set_version.py --bump major
```

The script uses `uv version` to synchronize `backend/pyproject.toml`, `backend/uv.lock` and `.env.docker.example`. Read-only validation:

```bash
uv run --project backend python scripts/set_version.py --check
```

Maintainers update versions for releases. Ordinary PRs should not bump the version. PR automation checks for accidental version drift.

## Debugging

Debug endpoints are disabled by default. Temporarily set `DEBUG_ENDPOINTS_ENABLED=true` and restart; turn it off after investigation.

```bash
curl -H "Accept-Language: en" http://127.0.0.1:8000/debug/stats \
  -H "Authorization: Bearer $XUWEN_API_KEY"
curl -H "Accept-Language: en" http://127.0.0.1:8000/debug/config \
  -H "Authorization: Bearer $XUWEN_API_KEY"
```

Inspect:

- `model_chain`: model requests and responses.
- `life`: current state and recent decisions.
- `database`: LanceDB timings.
- `calls`: LLM, retrieval, web-search/fetch statistics.

Retrieval debugging:

```bash
curl -H "Accept-Language: en" -X POST http://127.0.0.1:8000/memory/search \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $XUWEN_API_KEY" \
  -d '{"query":"What are you doing?","top_k":12}'
```

## Configuration development rules

- Add runtime settings to `Settings`, not hardcoded business logic.
- Update `.env.example` and both environment-reference pages.
- Choose conservative defaults for network, vision, writeback and debug capabilities.
- Store API keys as `SecretStr`; never expose plaintext in logs or debug responses.
- URL reading requires SSRF defenses: reject local/private/special addresses and limit redirects, timeouts, response size and injected prompt text.

## Documentation and comment style

User-facing documentation supports English and Chinese equally. Maintain corresponding pages in `docs/en/` and `docs/wiki/`, with shared mappings in `docs/pages.json`. Site navigation and fixed text live in one locale module per language under `docs/.vitepress/locales/`.

Keep identifiers in English. Existing explanatory comments/docstrings may remain Chinese; new user-visible messages must use the appropriate locale catalog and readable fixed fallback. Do not replace persisted data/protocol markers merely to translate code.

Pages and the Wiki are generated mirrors. Edit repository sources, not generated output or the Wiki website. See [Documentation maintenance](documentation.md).

## Before submitting

- Do not commit `backend/.env`, `.data/`, private exports or real API keys.
- Document new settings in `.env.example` and both language references.
- Update both API references for new interfaces.
- Add new pages in both languages and to `docs/pages.json`.
- Add plugin tests and documentation for new import formats.
- Run relevant tests, Ruff, mypy and documentation checks/builds.
