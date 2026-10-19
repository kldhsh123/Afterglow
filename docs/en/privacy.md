# Responsible use and data privacy

## Read before using

> If a conversation that has ended could continue in someone's familiar voice, would it still feel over?

That question motivates Afterglow, and it also makes its limits important.

### What this project can and cannot do

Afterglow uses real messages between you and another person to infer patterns: names, tone, jokes and conversational habits. A language model continues those patterns so written replies may resemble the person you remember.

**It produces text.** It does not restore that person's voice, appearance, physical presence or consciousness. It continues patterns of expression.

**Resemblance is not identity.** Improving the system can make differences less obvious, but it does not remove them. Generated replies are AI continuations, not statements by the original person.

### Who it may suit

- People who want to keep and revisit memories of a real relationship.
- People who understand that a familiar tone does not bring someone back.
- People who are emotionally stable enough to distinguish an AI continuation from the person.

### Who should avoid it

- If you are experiencing acute grief, depression or thoughts of self-harm, prioritize real people and professional support. Contact someone you trust, a qualified professional or a local crisis service; in immediate danger, contact your local emergency number. In mainland China, psychological assistance is available at 12356; Beijing also provides 010-82951332.
- If you intend to treat the system as proof that the person is still present, it may make reconnecting with real relationships harder.
- If the person is unaware and would not consent to the intended use, do not use their data to recreate them.

### Legal and ethical boundaries

Afterglow is a personal tool handling another person's real linguistic traces. The following uses are prohibited and may also violate applicable law:

- Harassment, stalking, coercion, blackmail or impersonation, regardless of whether the person is alive.
- Publishing, forwarding or redistributing private chat text, derived profiles, style analysis or synthetic replies without permission.
- Presenting generated text to third parties as the person's own words, including to relatives, friends or social-media audiences.
- Defamation, fraud, cyberbullying or other harm to the person being modeled.
- Importing chats without considering informed consent. If the person can be contacted, explain the use and seek permission. If they cannot be contacted, carefully consider whether they would agree; inability to obtain consent is not automatic permission.

Privacy and personality-rights laws differ and are evolving. You are responsible for assessing the legality and consequences of your use.

### Practical suggestions

1. You can stop at any time; you do not owe the system an explanation or farewell.
2. Do not send generated messages back to the person's real accounts, email or social profiles as an experiment.
3. If you find yourself angry, disappointed or personally hurt by the system, pause and consider whether you are treating it as the real person.
4. Keep returning to real relationships, including friends, family and professional support when needed.
5. Use it as a way to remember, without letting it replace your life outside the chat.

## Data privacy

- **Seek consent first.** Private chats contain jointly created sensitive material. Before export, import or sending text to model providers, confirm your right to process it and seek the other person's explicit consent wherever possible.
- **Local persistence.** Chats, vectors, persona cards, life state and image caches normally live in `backend/.data/` when started from `backend/`. The repository does not include automatic uploads of this data to a project-operated server.
- **Local storage does not mean no external processing.** Cloud models receive relevant text. For offline processing, point chat, embeddings, labeling, life state, vision, analysis and every other enabled model chain at local services; disable web search/fetch. The optional release-update check is a separate outbound request and can be disabled with `UPDATE_CHECK_ENABLED=false`.
- **Data that may leave your machine:**
  - Import sends cleaned text to your configured **Embedding API**, then stores vectors locally in LanceDB.
  - Optional labeling sends friend-message chunks to your **Label API** for mood/topic/importance tags.
  - Explicit relationship analysis sends cleaned blocks from both people to your **Analysis API**, potentially covering all selected source chats. Empty `ANALYSIS_*` settings fall back to the main chat provider.
  - Chat sends retrieved context, persona, life state and recent conversation to your **main chat API**.
  - With long-conversation summaries enabled, older conversation messages go to the `LIFE_*` model for incremental summarization; a local per-conversation cache stores the result. This is separate from long-term relationship memory.
  - Life-state and bare-domain confirmation calls send the current message, limited context and candidate domains to `LIFE_*`. That model returns state JSON or URL-intent decisions, not the final chat reply.
  - With `WEB_ACCESS_ENABLED=true` and an explicit search/current-information request, a query goes to **Tavily or SearXNG**.
  - With web access/fetch enabled, supplied public links may be fetched for title/body extraction. Bare domains pass local intent gating and `LIFE_*` confirmation first.
  - These services are selected, configured and paid for by you; Afterglow does not bundle third-party keys.
- **Review provider policies.** Configured providers or relays receive the corresponding chat, embedding and state inputs. Retention, training use and human-review policies vary by provider, not by Afterglow. Prefer clear no-training commitments for private third-party data, or run local inference such as Ollama/vLLM.
- **PII redaction is enabled by default.** Phone numbers, email, identity-document numbers, bank-card numbers and IP addresses are replaced before storage. QQ IDs, URLs and domains are intentionally retained for identity matching and context. Redaction is not a guarantee of anonymity.
- **Never commit `.env`.** It is ignored by Git, but you must still avoid sharing files containing API keys or passwords.
- **Application APIs require authentication by default**, except `/healthz`. Use `XUWEN_API_KEY` to protect model quota, memory and debug information. The configuration UI uses its own session token and access restrictions.
- **Exports contain plaintext.** Tools such as QQChatExporter include full chat content and account identifiers. Review everything before sharing an export.
- **Ordinary import is lightweight text import.** QQChatExporter's metadata-only media option preserves image filenames without downloading attachments, reducing export size and import time.
- **Historical images are optional and separate.** Preserve actual original files, for example under `resources/images/`, only if image retrieval is needed, then run `import-images` after text import. Originals stay in local cache, but their contents go to the configured vision service for descriptions.
