# Advanced and experimental features

These extensions are outside the basic wizard workflow. Configure them in `backend/.env` and invoke them explicitly through the CLI, main API or an external scheduler. Restart after changing environment settings.

| Feature | Status | Default behavior | Entry |
|---|---|---|---|
| Chat analysis | Advanced offline feature | Does not run automatically | `xuwen analyze` or `/analysis/*` |
| Historical image import | Advanced offline feature | Text placeholders only | `xuwen import-images` |
| Proactive messages | Independent experiment | Disabled; no automatic analysis or delivery | `xuwen analyze-proactive`, `/v1/companion/proactive/*` |

These features may process more private data than an ordinary chat turn. Read [Responsible use and privacy](privacy.md) and confirm permission to process both people's chats and images.

## Chat analysis

Analysis rereads original files with per-message timestamps; it does not reconstruct source evidence from LanceDB. It can produce:

- A relationship-event timeline and relationship stages.
- A regular personality report for the target person.
- Sleep, meals, activities and availability patterns.
- Optional experimental personality and relationship hypotheses, disabled by default.

### Model configuration

The block model extracts structured candidates; the final model merges across blocks. Empty settings fall back to the main chat model:

```dotenv
ANALYSIS_API_URL=
ANALYSIS_API_KEY=
ANALYSIS_MODEL=

ANALYSIS_FINAL_API_URL=
ANALYSIS_FINAL_API_KEY=
ANALYSIS_FINAL_MODEL=
```

See [analysis environment variables](environment.md#relationship-analysis) for token, concurrency and timeout settings.

### CLI usage

Run from `backend/`:

```bash
# Timeline and personality; all files must concern the same target person
uv run xuwen analyze chat.json --language en
uv run xuwen analyze source-a.json source-b.jsonl --language en

# One result type
uv run xuwen analyze chat.json --timeline-only --language en
uv run xuwen analyze chat.json --personality-only --language en

# Select one or more targets
uv run xuwen analyze chat.json --target timeline --target personality --language en
uv run xuwen analyze chat.json --target life --language en
uv run xuwen analyze chat.json --target experimental --language en

# Analyze from a month onward; successful block caches are reused
uv run xuwen analyze chat.json --since 2025-01 --language en

# Inspect deterministic blocks without model calls
uv run xuwen analyze chat.json --list-blocks --language en
uv run xuwen analyze chat.json --inspect-block BLOCK_ID --language en

# Explicit output language for this command
uv run xuwen analyze chat.json --language en
```

Without `--language`, output uses `XUWEN_LANGUAGE`, defaulting to English. Cached generated analysis is reused only for the same output language; legacy caches without language metadata are recomputed. Original quoted evidence remains unchanged.

Outputs go to `ANALYSIS_DATA_DIR`, usually `backend/.data/analysis/`:

| File | Purpose |
|---|---|
| `timeline.json` / `timeline.md` | Structured and readable timeline |
| `personality_report.json` / `personality_report.md` | Regular personality report |
| `personality_prompt_context.md` | Optional evidence-free summary for the main model |
| `life_profile.json` / `life_context.md` | Target person's life patterns |
| `manifest.json` | Inputs, cache and skipped-block statistics |
| `failures/*.json` | Refused/invalid-JSON blocks and diagnostics |

Analysis resumes incrementally. A block that is refused or repeatedly produces invalid JSON is recorded under `failures/` and skipped. It is not sent to the final model and does not invalidate successful blocks.

### Main API usage

With the full backend running:

| Method and path | Purpose |
|---|---|
| `POST /analysis/start` | Start an analysis task |
| `GET /analysis/{task_id}` | Task status |
| `GET /analysis/{task_id}/stream` | SSE progress |
| `POST /analysis/{task_id}/cancel` | Cancel |
| `GET /analysis/timeline` | Timeline results |
| `GET /analysis/personality` | Regular personality report |
| `GET /analysis/experimental` | Enabled experimental output |

Task API `files` must be inside `CONFIG_UI_UPLOADS_DIR`; use the CLI for other local paths. Send `Accept-Language: en` or `zh-CN` to choose generated output. See [analysis API](api.md#relationship-analysis-api).

### Use in chat and the life timeline

Generating a report does not automatically inject it. These switches default to off:

```dotenv
# Regular personality summary in the main chat prompt
ANALYSIS_PERSONALITY_PROMPT_ENABLED=false

# Life patterns as priors for the life-state model
ANALYSIS_LIFE_CONTEXT_ENABLED=false

# Experimental generation, then optional main-prompt use
ANALYSIS_EXPERIMENTAL_ENABLED=false
ANALYSIS_EXPERIMENTAL_PROMPT_ENABLED=false
```

Historical patterns are tendencies, not proof of present activities, personality, motives or health. Current conversation, today's timeline and retrieved human evidence take priority.

### Data scope

Analysis can send every cleaned block from selected files to the analysis service. Empty `ANALYSIS_*` fields use the main chat provider. For offline operation, configure both block and final models locally.

## Historical image import

Text import retains placeholders such as `[图片]` and resolvable image references. Historical image import is a separate offline stage: it describes original files with a vision model and indexes those descriptions in `history_images`.

### Prepare the export directory

Keep the chat file and resources together:

```text
export-dir/
  chat.json
  resources/
    images/
      image-a.jpg
```

Directory names can vary. The importer discovers export/image files recursively and matches originals using the chat's references.

### Configure and import

Historical image import requires a separate VLM configuration, even if the main chat model supports vision:

```dotenv
VISION_ENABLED=true
VISION_API_URL=https://example.invalid/v1
VISION_API_KEY=replace-with-your-key
VISION_MODEL=vision-model-name
```

Run from `backend/`:

```bash
# Import text first
uv run xuwen import chat.json --language en

# Import corresponding historical images
uv run xuwen import-images export-dir --language en

# Force a plugin if automatic detection is insufficient
uv run xuwen import-images export-dir --plugin afterglow_v1 --language en
```

The importer:

1. Extracts image references and surrounding context.
2. Deduplicates by SHA-256 and caches originals in `IMAGE_DATA_DIR`.
3. Generates one description per unique image.
4. Stores descriptions, timestamps, senders and context in `history_images`.
5. Attempts indexing when vector tables reach the configured threshold.

Rerunning reuses cached descriptions and indexed records. `uv run xuwen stats --language en` shows `history_images` counts.

### Limits and privacy

- The plugin must extract usable references; an unqualified `[图片]` cannot locate an original.
- Remote CDN and data URLs are not downloaded. Export expiring resources to local files first.
- Missing originals, images above `VISION_MAX_IMAGE_BYTES` and failed descriptions are skipped.
- Original files are cached locally, but their contents are sent to your VLM; cloud retention/training policies depend on that provider.
- Chat normally retrieves textual descriptions rather than resending all historical originals.

See [historical image imports](importing.md#historical-images).

## Proactive messages (independent experiment, disabled by default)

> **Maintainer's note:** The current experience is not considered good enough for general use. It depends on plentiful, continuous history with natural conversation openings; the maintainer lacks sufficient data to validate and tune it thoroughly. Treat this as a best-effort experiment, not a mature feature.

Proactive messages, ordinary imports and relationship analysis are independent paths:

| Path | When it runs | Purpose |
|---|---|---|
| `xuwen import` | Explicit import only | Vectors and a legacy lightweight proactive profile; no proactive AI attribution and no indication that data is suitable for automatic messaging |
| `xuwen analyze` | Explicit relationship analysis only | Timeline, personality and Life reports; does not create/update proactive analysis |
| `xuwen analyze-proactive` | Separately requested | Full proactive report, optionally using AI to attribute conversation-opening motives |

Do not enable messaging merely because import or analysis succeeded. Run separate proactive analysis, check quality, then decide manually.

### Run proactive analysis separately

From `backend/`:

```bash
uv run xuwen analyze-proactive chat.json --language en
uv run xuwen analyze-proactive source-a.json source-b.jsonl --language en

# Optional date limit; matching AI attributions are reused by default
uv run xuwen analyze-proactive chat.json --since 2025-01 --language en
uv run xuwen analyze-proactive chat.json --no-resume --language en
```

The command writes `ANALYSIS_DATA_DIR/proactive_analysis.json`: conversation openings by both people, the friend's initiative frequency, silence gaps, hour/weekday/month distributions and optional AI motive attribution. It does not generate timeline, personality or Life reports.

AI attribution is independently controlled by:

```dotenv
# true: final analysis model adds motive/time explanations
# false: local deterministic statistics only
ANALYSIS_PROACTIVE_ENABLED=true
```

Attribution happens only during explicit `analyze-proactive`, never during ordinary import, relationship analysis, startup or polling. Model explanations are uncertain hypotheses, not the person's own causal account.

### Check data quality first

Run the read-only check:

```bash
uv run python scripts/check_proactive_quality.py
```

`scripts/analyze_persona.py` and `scripts/check_proactive_quality.py` are separate helper scripts without a `--language` option; some console messages remain Chinese. Do not append that option to them. Use the main CLI with `--language en` or `--language zh-CN` for imports and analysis that need an explicit language.

It does not call AI, modify reports or enable proactive messages. It considers valid long-gap samples, placeholders, concrete-topic samples, `other` classifications and AI attribution coverage:

- `ready`: passes current rules; you still decide whether to enable it.
- `limited`: useful for observation, not recommended for automatic sending.
- `not_ready`: do not enable it.

These are conservative hints, not guarantees. Even with `ready`, inspect manual output before connecting real contacts or unattended bots.

The feature learns approximate opening times, silence gaps, previous context and opening types from stored windows, then combines them with Life state, daily caps and quiet hours. Opening quality, topic choice and disturbance prevention are still unstable; it is disabled by default and not recommended for production.

Proactive generation, hooks, rewrites and fixed fallbacks always use backend `XUWEN_LANGUAGE`, independently of the caller's page language or `Accept-Language`. Existing stored source material is not translated.

### What the backend does not deliver automatically

The backend does not install cross-device timers or directly push messages to the web, QQ, WeChat or other IMs. A frontend, bot or scheduler must:

1. Call polling or decision endpoints.
2. Respect the next-check timestamp.
3. Deliver only when `should_send=true` or `state="sent"`.
4. Use a stable `conversation_id`, and `caller_id` when distinguishing callers.

### Profile fallback order

Runtime scheduling selects:

1. Valid independent `proactive_analysis.json` with usable samples.
2. Legacy `.data/persona/proactive_profile.json` from ordinary import.
3. A profile rebuilt from stored `dialogue_windows`.
4. A conservative empty profile if no data is available.

Fallback supports old data; it does not make messaging suitable without independent analysis. Check `profile_source="analysis"` when expecting the complete report.

### Enable gating

Minimal settings:

```dotenv
PROACTIVE_ENABLED=true
PROACTIVE_SCORE_THRESHOLD=0.55
PROACTIVE_CHECK_INTERVAL_SECONDS=900
PROACTIVE_MIN_IDLE_MINUTES=180
PROACTIVE_MAX_PER_DAY=1
PROACTIVE_QUIET_HOURS=00:00-08:30
PROACTIVE_SKIP_WHEN_LIFE_BUSY=true
```

Runtime gates activate only after explicit `PROACTIVE_ENABLED=true`. See [proactive settings](environment.md#proactive-messages-experimental).

### Choose an endpoint

| Method and path | Use |
|---|---|
| `POST /v1/companion/proactive` | Immediate opening; bypasses learned gates; manual debugging only |
| `POST /v1/companion/proactive/decide` | Immediate decision without a pending candidate |
| `POST /v1/companion/proactive/poll` | Preferred experimental entry; maintains candidates and returns `next_poll_at_ms` |
| `GET /v1/companion/proactive/profile` | Learned profile and recent audit records |
| `GET /analysis/proactive` | Read-only independent report |

Example poll:

```json
{
  "conversation_id": "conversation-placeholder",
  "caller_id": "client-placeholder",
  "reason": "learned_rhythm",
  "auto_send": true
}
```

The first call typically returns `state="scheduled"` and `next_poll_at_ms`. Wait until that time before polling again. If due and allowed, `auto_send=true` produces `sent` or `silenced`; generated text is in `proactive.message`.

Messages through `/v1/chat/completions` or `/v1/responses` record user activity and cancel unsent candidates. If your external system bypasses those routes, maintain activity timestamps and pass `last_user_message_at_ms`.

### Disturbance-prevention boundaries

- Keep daily limits and quiet hours; do not disable every gate to increase trigger frequency.
- With `PROACTIVE_SKIP_WHEN_LIFE_BUSY=true`, busy/sleeping/away Life states prevent initiation.
- The system has no reliable online, read-receipt or phone-attention signals; output does not prove presence.
- Recent decisions are visible in the profile endpoint; full audit is `.data/persona/proactive_audit.jsonl`.
- Output can still be inappropriate or repetitive. The delivery system must retain a final off switch and human control.

See [proactive API](api.md#post-v1companionproactive-experimental-not-recommended).
