# Quick start

> [!TIP]
> The maintainer recommends running from source: it makes configuration changes and advanced or experimental features easier to work with.

## Requirements

| Tool | Requirement | Purpose |
|---|---|---|
| Python | 3.12.x (`>=3.12,<3.13`) | Backend runtime |
| [uv](https://github.com/astral-sh/uv) | Current stable release | Python dependencies |
| Node.js | 20 or later | Only needed to build the frontends or documentation |
| pnpm | Current stable release | Main chat frontend |

Afterglow does not include a model. You need at least an OpenAI-compatible chat model and an embedding service. Supported exports include QQChatExporter, WeFlow, Douyin Chat Export, WhatsApp, Telegram Desktop, DiscordChatExporter and the Afterglow Chat intermediate format. See [Importing chats](importing.md).

## Recommended: setup wizard

You do not need to create `.env` before the first start:

```bash
cd backend
uv sync --extra dev
XUWEN_LANGUAGE=en uv run uvicorn xuwen.chat_api.app:create_app --factory --reload
```

When required settings are missing, the console prints a setup URL and a session token. Open `http://127.0.0.1:8000/config/`, enter that token and complete the wizard:

1. Identify both people from one or more chat files.
2. Select the relationship, persona template and backend language.
3. Configure and test the main chat model.
4. Configure and test embeddings; optionally enable labeling.
5. Select optional life timeline, vision, web access and response policy features.
6. Configure Query Rewrite and a cross-encoder reranker.
7. Import chats and generate persona, style and circadian profiles.
8. Set the backend access password, `XUWEN_API_KEY`.

The wizard writes `backend/.env` and backs up previous versions in `.env-backups/`. Stop and restart the backend after setup. The automatically enabled wizard then closes unless explicitly enabled.

The page language follows your browser on first use: Simplified or Traditional Chinese selects Chinese; other languages select English. The language dropdown remembers your choice. Provider presets appear only in Chinese. For empty English configurations, chat defaults to Google's OpenAI-compatible Gemini endpoint; embeddings and auxiliary models default to OpenAI. Existing configuration and user input are preserved; API keys must be supplied by you.

**Language settings**

Startup and CLI examples on this page explicitly select English; the Chinese page selects Chinese. The documentation and frontend language dropdowns change the interface, not the backend language.

| Setting | English / Chinese | Scope |
|---|---|---|
| Backend `.env` | `XUWEN_LANGUAGE=en` / `XUWEN_LANGUAGE=zh-CN` | Startup messages, default output and proactive messages; English when unset; fully restart after changes |
| CLI option | `--language en` / `--language zh-CN` | Overrides messages and supported generated content for one command; does not save to `.env` |
| HTTP header | `Accept-Language: en` / `Accept-Language: zh-CN` | Overrides request-scoped messages and generated analysis; proactive messages still follow backend configuration |

CLI precedence is `--language` → process environment variable `XUWEN_LANGUAGE` → `XUWEN_LANGUAGE` in the selected `.env` → default `en`. The `XUWEN_LANGUAGE=en` startup prefix is Bash syntax for Linux, macOS and WSL. It overrides `.env` for that process; it is not a Uvicorn option. For a persistent preference, save `XUWEN_LANGUAGE=en` in `backend/.env`; subsequent startup commands can omit the prefix.

In Windows PowerShell, start from `backend/` with:

```powershell
$env:XUWEN_LANGUAGE = "en"
uv run uvicorn xuwen.chat_api.app:create_app --factory --reload
```

CLI `--language` has the same syntax in Bash and PowerShell and can precede or follow the subcommand:

```bash
uv run xuwen --language en --help
uv run xuwen stats --language en
```

HTTP messages and generated analysis output use `Accept-Language` when supplied, otherwise the backend language. Normal chat follows explicit language requests and the conversation. Original stored messages and custom prompts are not translated.

To edit configuration without starting the full backend:

```bash
cd backend
uv run python -m xuwen.web_ui --language en
```

Open `http://127.0.0.1:8765/config/` and use the token printed by that process.

## Manual configuration

```bash
cd backend
uv sync --extra dev
cp .env.example .env
```

Fill in identities, chat model, embeddings and API keys; see [Configuration](configuration.md). Then import history:

```bash
uv run python -m xuwen.ingestion.cli import path/to/chat.json --language en
uv run python scripts/analyze_persona.py path/to/chat.json
XUWEN_LANGUAGE=en uv run uvicorn xuwen.chat_api.app:create_app --factory --reload
```

`scripts/analyze_persona.py` and `scripts/check_proactive_quality.py` are separate helper scripts without a `--language` option; some console messages remain Chinese. Do not append that option to them. Use the main CLI with `--language en` or `--language zh-CN` for imports and analysis that need an explicit language.

See [Importing chats](importing.md) for multiple files, JSONL, WeChat, images and persona aggregation.

## Start the frontend (optional)

The chat frontend provides local conversations, memory sources and diagnostics:

```bash
cd frontend
pnpm install
pnpm dev
```

Open `http://127.0.0.1:5173/`. Frontend dependencies are unnecessary if you only use the backend API.

## Connect an OpenAI-compatible client

Afterglow provides Chat Completions and Responses APIs. A minimal request:

```bash
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <XUWEN_API_KEY>" \
  -H "Accept-Language: en" \
  -d '{
    "messages": [{"role": "user", "content": "Are you there?"}],
    "conversation_id": "my-conv-1"
  }'
```

The request's `model` is accepted for compatibility; `.env`'s `CHAT_MODEL` selects the actual model. See [Backend API](api.md).

## Docker

Use [Docker deployment](docker.md) if you prefer not to install Python and uv. Source and container deployments can share `.env` and `.data/` if started from the same working directory. Avoid running both on the same port.

## Health checks

```bash
curl http://127.0.0.1:8000/healthz
curl -H "Accept-Language: en" -H "Authorization: Bearer <XUWEN_API_KEY>" \
  http://127.0.0.1:8000/readyz
```

`/healthz` is the only application API endpoint exempt from authentication by default. The setup wizard has its own session-token authentication.
