# Exporting chat history

This guide explains how to prepare files for every built-in Afterglow importer. Exporter interfaces change between releases; use the linked upstream instructions for installation and account access, and select the formats below.

## Before exporting

Confirm that you have permission to process the conversation; see [Responsible use and privacy](privacy.md). Export one conversation with the person you want to model. Keep an untouched local backup, retain original timestamps and account identifiers, and avoid posting exports or account credentials in issues. Afterglow does not need your platform login cookie or token to import a file.

| Source | Select this output | File to import |
|---|---|---|
| QQChatExporter | JSON or chunked JSONL | Chat `.json`, or all `chunks/*.jsonl` |
| WeFlow / WeChat | Original `arkme-json`, or ChatLab JSONL | Exported `.json` / `.jsonl` |
| Douyin Chat Export | ChatLab JSON / JSONL | Exported `.json` / `.jsonl` |
| WhatsApp Web (desktop browser) | Compatible TXT or Markdown | Exported `chat.txt` or `chat.md` |
| Telegram Desktop | Human-readable HTML | `messages.html` and every numbered continuation |
| DiscordChatExporter | JSON | Exported channel `.json` |
| Other sources | Convert to Afterglow Chat v1 | `.json` / `.jsonl` |

Choosing HTML, Excel or CSV in another exporter does not make it compatible with the Telegram HTML importer. File extensions alone do not identify a supported schema.

## QQ: QQChatExporter

Upstream: [project and installation instructions](https://github.com/shuakami/qq-chat-exporter) · [releases](https://github.com/shuakami/qq-chat-exporter/releases) · [usage documentation](https://shuakami.github.io/qq-chat-exporter/).

1. Install a release suitable for your system, start the exporter and sign in following its instructions. Current desktop instructions open the local interface at `http://localhost:40653/qce`; use the startup console's address/token if yours differs.
2. Select the friend's conversation and date range. Choose **JSON** for one file, or **chunked JSONL** when available for large histories. Preserve identity and message metadata.
3. For text import, metadata-only media is sufficient. If you plan to import historical images, retain the corresponding local media/resource directories as well.
4. In Afterglow, upload the conversation JSON or **all JSONL files under `chunks/`**. Do not import an index-only `manifest.json`. Chunked exports may omit the current account identity: verify both people in the setup wizard.

Do not choose QQChatExporter's HTML/TXT/Excel outputs for its JSON importer.

## WeChat: WeFlow

Upstream: [WeFlow](https://github.com/hicccc77/weflow) · [releases](https://github.com/hicccc77/weflow/releases).

1. Follow the exporter release's instructions to open your local WeChat data. Complete its local data-access setup before exporting.
2. Select the target private conversation and date range, then choose the original **JSON / `arkme-json`** format or **ChatLab JSONL**. Menu wording depends on version.
3. Keep the complete file, including sender/member records and metadata. For `arkme-json`, expect `session`, `senders` and `messages`; ChatLab exports identify the generator as `weflow` and platform as `wechat`.
4. Upload the exported JSON/JSONL to Afterglow and verify self/friend accounts. Preserve local images alongside the export for a later `import-images` run.

WeFlow also offers HTML, spreadsheets, plain text and database exports; those are not its supported Afterglow input formats. Its upstream now promotes Alpha as a successor; do not assume an Alpha export has the same supported schema without checking it.

## Douyin: Douyin Chat Export

Upstream: [Douyin Chat Export](https://github.com/TeamBreakerr/douyin-chat-export) · [releases](https://github.com/TeamBreakerr/douyin-chat-export/releases).

1. Start the exporter using its Docker or source instructions. Its default port is `8000`, also used by Afterglow; stop one service or map the exporter to another local port, for example `127.0.0.1:8001:8000` in Docker Compose.
2. Open its `/panel`, sign in using the upstream's supported login methods and collect the desired web direct-message conversation.
3. Select that private conversation and export **ChatLab JSON or JSONL**, including metadata/member records. Afterglow currently supports private Douyin chats only.
4. Upload the file to Afterglow. It uses `meta.ownerId` to suggest your account and the most active other member as friend; confirm both suggestions.

Text, links and replies are retained. Media becomes placeholders. Expiring CDN URLs or data URLs in a Douyin export are not downloaded by `import-images`.

## WhatsApp: desktop browser

Use [WhatsApp Web](https://web.whatsapp.com/) on your computer. This guide covers the desktop browser workflow; the native desktop app does not expose the phone app's export-chat menu.

1. Sign in to WhatsApp Web and open the target private conversation. Wait for history to sync, then load the date range you need. The browser may contain only part of your history: check the first and last exported messages.
2. Use a desktop browser export tool that produces a compatible **TXT** or **Markdown** file. Follow that tool's installation and export instructions. WhatsApp Web itself does not provide a universal export button; do not assume all extensions produce the same format.
3. Preserve names, dates and message boundaries. For TXT, the importer accepts timestamped lines such as `[10/13/26, 16:25:24] Friend: hello`. Compatible Markdown uses a `# WhatsApp` title, date headings such as `## 2026年10月13日`, and lines such as `[16:25] **Friend:** hello`. Arbitrary Markdown or JSON exports need conversion before import.
4. Upload `chat.txt` or a compatible `chat.md`. If both represent the same chat, import only one. Confirm both identities in the wizard; IDs are derived from display names.

Markdown timestamps have minute precision. See [import date interpretation](importing.md#whatsapp-and-telegram-imports) for date-order ambiguity and `APP_TIMEZONE`. WhatsApp attachments are not automatically imported as historical images. Do not upload an archive, HTML page or an unsupported extension-specific JSON file as a TXT/Markdown chat.

## Telegram: Telegram Desktop

Upstream: [official export announcement](https://telegram.org/blog/export-and-more) · [Telegram Desktop](https://desktop.telegram.org/).

1. Open the target conversation in **Telegram Desktop**. Use the conversation's **Export chat history** action; the general export is under **Settings → Advanced → Export Telegram data**.
2. Choose **Human-readable HTML**. Select the desired date range. Enable photo export if historical image retrieval is needed.
3. Finish the export and retain its full directory. A large conversation may contain `messages.html`, `messages2.html`, `messages3.html`, and so on.
4. Upload **all pages belonging to that conversation** to Afterglow in one task. Preserve photos and supported static stickers at their original relative paths for later image import.

The importer reads the HTML structure and timestamp attributes, independently of the displayed language. Mobile and web clients do not provide this Desktop export flow. If a general export contains many conversations, select only files for the intended person. The wizard derives IDs from display names, so verify both identities.

## Discord: DiscordChatExporter

Upstream: [DiscordChatExporter](https://github.com/Tyrrrz/DiscordChatExporter) · [releases](https://github.com/Tyrrrz/DiscordChatExporter/releases) · [usage and credential guidance](https://github.com/Tyrrrz/DiscordChatExporter/wiki).

1. Download the GUI or CLI release for your system. Follow its credential/access instructions; prefer an authorized bot where channel access permits, and review Discord's account-automation rules in the upstream guide.
2. Select the direct-message conversation or a channel you are authorized to export, then choose **JSON**. For a persona of one person, a direct conversation makes identity assignment clearer than a multi-person channel.
3. Export the required date range. If historical images are needed, use the exporter's media-download option and keep local files beside the JSON at the paths referenced in it.
4. Upload the `.json` file to Afterglow and assign the account IDs for self/friend. Remote CDN references remain placeholders; `import-images` only reads relative image files inside the export directory.

Discord's official personal-data package is a different format and does not include the other person's replies. HTML, TXT and CSV from DiscordChatExporter are not its supported Afterglow format. Never include the exporter login token in the file you upload to Afterglow.

## Other sources: Afterglow Chat v1

[Afterglow Chat v1](chat-format.md) is an intermediate data format, not a platform login/export tool.

1. Export the original platform's messages using an appropriate tool and retain an untouched backup.
2. Convert them according to the [format specification](chat-format.md): preserve stable message IDs, sender IDs, timestamps in milliseconds and reply references; include the required format metadata for your JSON/JSONL variant.
3. Keep participants identifiable as self/friend, and retain local media at safe relative paths if you need historical images.
4. Test with a small sanitized sample, then import the full conversation. For reusable public support, implement a dedicated [ingestion plugin](development.md).

## Verify and import

Check that both sides, the expected date range and original IDs are present. Use the [configuration editor](config-editor.md) or setup wizard to configure model services and identities, then follow [Importing chats](importing.md).

```bash
cd backend
uv run xuwen import /path/to/chat.json --language en
uv run xuwen import /path/to/chunks/*.jsonl --language en
uv run xuwen import /path/to/TelegramExport/messages*.html --language en
# Optional: requires local image files and a configured vision model
uv run xuwen import-images /path/to/export-directory --language en
```

One import task must describe the same target person. Do not merge all conversations from an account-wide export into one persona. Keep original media directories if you want image retrieval: uploading text files through the wizard does not upload an entire media folder.
