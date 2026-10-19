# FAQ

**What is the setup wizard? Is it required?**
It is optional but recommended for first use. Missing required settings automatically enables `/config/` and prints a session token. The eight steps save `.env`, import chats and generate the persona. See [manual setup](quickstart.md#manual-configuration) for the alternative.

**Must I use Alibaba Cloud Qwen3-Embedding?**
No. Use any compatible `/embeddings` service, including a local one, and set `EMBEDDING_API_URL`, `EMBEDDING_MODEL` and `EMBEDDING_DIM`. Changing models after importing requires rebuilding the vector database; see [Troubleshooting](troubleshooting.md#import-fails-with-spill-has-sent-an-error).

**Must I use OpenAI?**
No. The main model supports compatible Chat Completions and Responses upstreams, selected by `CHAT_API_PROTOCOL=chat_completions|responses`. DeepSeek, Moonshot, Qwen and Ollama commonly provide Chat Completions. Verify your provider's actual endpoints.

**Can I turn off PII redaction?**
Yes: `ENABLE_PII_REDACTION=false`. Alternatively, load custom rules with `PII_RULES_PATH`.

**Why are QQ IDs and URLs not redacted?**
QQ IDs are needed to associate senders; shared URLs carry conversational context. Built-in redaction targets phone numbers, email, identity documents, bank cards and IP addresses. Check the rules against your own data rather than assuming every identifying detail is removed.

**Can I import WeChat, Telegram or Discord?**
Yes. WeChat supports [WeFlow](https://github.com/hicccc77/weflow) `arkme-json` and ChatLab JSONL; Telegram supports Desktop HTML exports; Discord supports [DiscordChatExporter](https://github.com/Tyrrrz/DiscordChatExporter) JSON. WhatsApp TXT/Markdown and QQChatExporter are also supported. Other platforms can use an ingestion plugin or [Afterglow Chat v1](chat-format.md).

**Will the companion drift as I chat?**
Turns are asynchronously written to `live_messages` with lower trust than human history. AI messages are excluded from cross-conversation semantic retrieval by default. You can pause writeback in frontend settings. Enabling long-term AI retrieval may increase drift.

**How do I delete a memory?**
Use `DELETE /memory/friend_messages/{chunk_id}` or `DELETE /memory/response_pairs/{pair_id}`. These perform soft deletion.

**Can everything run locally?**
Yes. Use local chat and embedding services, configure every enabled auxiliary model locally, and disable web access. See [offline configuration](configuration.md#fully-offline-operation).

**Why is output in a different language from the UI?**
The page language and backend language are separate. Set `XUWEN_LANGUAGE=en|zh-CN` and restart for startup/proactive output; use CLI `--language` or HTTP `Accept-Language` for those scopes. Chat history and existing stored reports are not automatically translated.
