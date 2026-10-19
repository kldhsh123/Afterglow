# Importing chats

Before importing, confirm that you have permission to process the shared chat content and read [Responsible use and privacy](privacy.md). Never submit unredacted real exports to GitHub, issues or pull requests.

Need to export first? Follow the [platform export tutorials](exporting.md).

## Supported formats

| Source | Format | Built-in plugin |
|---|---|---|
| Afterglow Chat v1 | JSON, typed/bare JSONL | `afterglow_v1` |
| [QQChatExporter](https://github.com/shuakami/qq-chat-exporter) | JSON, chunked JSONL | `qqexporter_v5` |
| [WeFlow](https://github.com/hicccc77/weflow) | `arkme-json`, ChatLab JSONL | `wechat_weflow` |
| [Douyin Chat Export](https://github.com/TeamBreakerr/douyin-chat-export) | ChatLab JSON, JSONL | `douyin_chat_export` |
| WhatsApp | TXT, Markdown | `whatsapp` |
| Telegram Desktop | HTML | `telegram_html` |
| [DiscordChatExporter](https://github.com/Tyrrrz/DiscordChatExporter) | JSON | `discord_chat_exporter` |

WeChat imports use third-party [WeFlow](https://github.com/hicccc77/weflow) exports in `arkme-json` or ChatLab JSONL. Assess access permissions and privacy risks first; prefer an isolated offline environment for handling exports.

Other platforms can convert to [Afterglow Chat v1](chat-format.md) or implement an ingestion plugin. A dedicated plugin is preferable for long-term public use or complete preservation of platform semantics.

## WhatsApp and Telegram imports

For WhatsApp, choose `chat.txt` from the export directory. `chat.md` is also supported; if both contain the same chat, import only one. Markdown timestamps have minute precision. TXT parsing supports bracketed timestamps, Android date-dash lines, 12/24-hour clocks, multiline messages and Unicode directional controls. Dot-separated dates use day/month order. Slash-separated dates default to month/day; if a first date component exceeds 12 anywhere in the file, the entire file uses day/month. Timestamps without a zone use `APP_TIMEZONE` (default `Asia/Shanghai`).

For Telegram Desktop, choose `messages.html` and all paginated files such as `messages2.html`. Parsing uses HTML structure and timestamp attributes, independently of displayed date language, directory or file names. It preserves senders for consecutive messages, reply targets, media captions and placeholders. Untimed date separators and notifications are not chat messages.

```bash
uv run xuwen import path/to/WhatsAppExport/chat.txt --language en
uv run xuwen import path/to/TelegramExport/messages.html --language en
uv run xuwen import-images path/to/TelegramExport --plugin telegram_html --language en
```

These exports do not provide sender account UIDs. Plugins derive identifiers from display names, prefixed with `whatsapp:` or `telegram:`. Confirm “self” and “friend” in the wizard; manually review name changes and identical display names. Message text can be in any language. WhatsApp media-placeholder recognition currently covers Chinese and English; descriptions in other languages remain as original text.

Telegram image import reads local original images and static stickers. Animations, audio and video remain placeholders. WhatsApp exports without media keep placeholders only. Uploading Telegram HTML imports text; preserve the complete export directory and run `import-images` separately for images.

## DiscordChatExporter imports

Use JSON exported by [DiscordChatExporter](https://github.com/Tyrrrz/DiscordChatExporter), including account IDs, timestamps and replies. Detection does not depend on filenames or interface language. Assign “self” and “friend” in the wizard. Discord's official personal-data `package` is a different format and does not contain the other person's replies.

```bash
uv run xuwen import "path/to/Direct Messages - Friend [channel-id].json" --language en
```

Attachments and stickers become media placeholders; text and attachment descriptions remain unchanged. `import-images` only accepts relative paths to images inside the export directory. Remote CDN URLs are not downloaded automatically.

## Import with the wizard

Upload one or more `.json`, `.jsonl`, `.ndjson`, `.txt`, `.md`, `.html` or `.htm` files. Each file is independently inspected for format and candidate UIDs. Across accounts or platforms, assign all your UIDs to “self” and all the target person's UIDs to “friend”.

Every file in a single task must concern the same target person. The wizard aggregates that task's files to build persona, style and circadian profiles.

## CLI text import

```bash
cd backend

# One file
uv run python -m xuwen.ingestion.cli import chat.json --language en

# Multiple files: JSON/JSONL and built-in formats may be mixed
uv run python -m xuwen.ingestion.cli import qq.json wechat.json douyin.json chunks/*.jsonl --language en

# Force a format; do not use for mixed formats
uv run python -m xuwen.ingestion.cli import chat.jsonl --plugin afterglow_v1 --language en
```

Multi-file imports reuse the database connection and embedding client. Cleaning, session splitting and chunk construction remain per-file, so vector windows and response pairs do not cross file boundaries. After import, the circadian profile is rebuilt from deduplicated messages in all supplied files; the lightweight proactive profile is rebuilt from all stored windows.

Use `--language en|zh-CN` for command output. Without it, `XUWEN_LANGUAGE` from the environment or selected `.env` is used, defaulting to English. This does not translate the original chat content.

## QQChatExporter JSONL

Select `chunks/*.jsonl`, not the index-only `manifest.json`:

```bash
uv run python -m xuwen.ingestion.cli import path/to/chunks/*.jsonl --language en
```

Chunked JSONL usually lacks the current account identity stored in the manifest. Confirm both UIDs manually in the first-time wizard.

For text-only imports, QQChatExporter's metadata-only media option is sufficient. Preserve actual image resources separately if historical image retrieval is needed.

## WeFlow ChatLab JSONL

```bash
uv run python -m xuwen.ingestion.cli import path/to/wechat.jsonl --language en
```

ChatLab member records do not reliably identify which account is yours. Assign identities in the wizard.

## Douyin Chat Export

Use ChatLab JSON or JSONL from [Douyin Chat Export](https://github.com/TeamBreakerr/douyin-chat-export):

```bash
uv run python -m xuwen.ingestion.cli import path/to/douyin.jsonl --language en
```

The plugin identifies self through `meta.ownerId` and suggests the most active non-owner member as friend. Verify this in the wizard. Text, shared links and replies are preserved. Images, stickers, audio, video and shares without text remain placeholders in dialogue windows; they do not become standalone friend messages or response pairs.

Douyin image exports contain expiring CDN or data URLs. Text import keeps `[图片]` placeholders. `import-images` does not download these remote resources or put their URLs/base64 into the vector database.

## Emoji placeholder mode

Default `raw` mode normalizes common QQ/WeChat bracketed emoji. If middleware already normalized textual emoji to `[/表情]`, use:

```bash
uv run python -m xuwen.ingestion.cli import chat.json \
  --json-emoji-mode normalized --language en
```

Use the same `--json-emoji-mode` for import and persona analysis. Existing placeholder markers are data conventions and do not change with the UI language.

## Multi-file persona

```bash
# One file
uv run python scripts/analyze_persona.py chat.json

# Multiple files, all for the same target person
uv run python scripts/analyze_persona.py qq.json wechat.json chunks/*.jsonl
```

`scripts/analyze_persona.py` and `scripts/check_proactive_quality.py` are separate helper scripts without a `--language` option; some console messages remain Chinese. Do not append that option to them. Use the main CLI with `--language en` or `--language zh-CN` for imports and analysis that need an explicit language.

The persona aggregator deduplicates across files and groups by ingestion plugin. Within each plugin timeline, it sorts messages and re-splits sessions, allowing reply samples across same-format chunk boundaries. All plugins contribute to aggregate language and circadian statistics, but cross-platform response pairs are not constructed.

Outputs go to `PERSONA_DATA_DIR`:

- `persona_card.md`
- `persona_report.json`
- `persona_style_profile.json`
- `circadian_profile.json`

## Historical images

Text import only retains media placeholders and image references. Preserve the full export directory for image retrieval:

```text
export-dir/
  chat.json (or chat.jsonl)
  resources/ (or media/)
    any-subdirectory/
      image.jpg
```

Set `VISION_ENABLED=true` and `VISION_API_*`, then run:

```bash
uv run python -m xuwen.ingestion.cli import-images export-dir --language en
```

The command recursively discovers exports and images, deduplicates by SHA-256, generates descriptions with the vision model and writes `history_images`. Original images are not resent to the main model on every chat.

## Resume interrupted work

Text chunk IDs are deterministic. Rerun the same command after an interrupted import or embedding request; stored portions are skipped. If labeling is enabled, finish remaining labels separately:

```bash
uv run python -m xuwen.ingestion.cli label --language en
```

If some embedding requests fail, the configuration wizard reports the missing chunk count and offers a retry. Import the same files again to fill those gaps; skipping records already stored during a normal resume is not a failure.

Discord, Telegram, and WhatsApp message and response-pair IDs no longer depend on their position in an export. Re-exporting a different date range can therefore reuse IDs for the same messages and response pairs. Existing database records created with the previous ID scheme are not migrated automatically. Correcting previously damaged text, mismatched images, or duplicate records requires separate cleanup or rebuilding from the original exports.
