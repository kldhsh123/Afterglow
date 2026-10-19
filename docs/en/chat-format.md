# Afterglow Chat v1 format

Afterglow v1 is a stable, platform-independent intermediate format for third-party middleware. It currently supports private chats only. A one-off converter, including one written with AI assistance, can adapt other exports to reuse the import pipeline.

Converters suit experiments and personal migration. For long-term public use or complete source semantics, implement a dedicated ingestion plugin and submit a PR. Plugins keep detection, identity inspection, message parsing and image references together without losing details in conversion.

JSON format:

```json
{
  "afterglow": {"format": "afterglow-chat", "version": "1.0"},
  "conversation": {"id": "conv-001", "type": "private", "title": "Optional title"},
  "participants": [
    {"uid": "me", "name": "Me", "role": "self"},
    {"uid": "friend", "name": "Friend", "role": "friend"}
  ],
  "messages": [
    {
      "id": "m1",
      "seq": 1,
      "timestamp_ms": 1783625485000,
      "sender_uid": "friend",
      "sender_name": "Friend",
      "sender_role": "friend",
      "kind": "text",
      "raw_type": "text",
      "text": "How was your day?"
    }
  ]
}
```

Message fields:

- `timestamp_ms` must be a Unix timestamp in milliseconds.
- `sender_role` is `self`, `friend`, `system` or `other`. If absent, identity falls back to `participants` and UIDs in `.env`.
- `kind` uses `text`, `reply`, `placeholder`, `recalled`, `system` or `unknown`.
- Ordinary text import preserves image placeholders and references without calling vision:

```json
{
  "id": "m2",
  "seq": 2,
  "timestamp_ms": 1783625493000,
  "sender_uid": "friend",
  "sender_role": "friend",
  "kind": "placeholder",
  "raw_type": "image",
  "text": "[图片: a.jpg]",
  "placeholders": ["[图片]"],
  "attachments": [{"type": "image", "name": "a.jpg"}]
}
```

`[图片]` is an existing import data marker; keep it when using this convention, regardless of the UI language. Put images under `resources/images/`:

```text
export-dir/
  chat.json
  resources/
    images/
      a.jpg
```

After text import, run image import explicitly:

```bash
uv run python -m xuwen.ingestion.cli import-images export-dir --plugin afterglow_v1 --language en
```

Two JSONL forms are supported. Typed records using `_type=header/participant/message` are recommended:

```jsonl
{"_type":"header","afterglow":{"format":"afterglow-chat","version":"1.0"},"conversation":{"id":"conv-001","type":"private"}}
{"_type":"participant","uid":"me","name":"Me","role":"self"}
{"_type":"participant","uid":"friend","name":"Friend","role":"friend"}
{"_type":"message","id":"m1","seq":1,"timestamp_ms":1783625485000,"sender_uid":"friend","sender_name":"Friend","kind":"text","text":"How was your day?"}
```

Alternatively, each line can be a bare message object. Every line must contain `sender_uid` (or `senderUid`) and `timestamp_ms` (or `timestamp`). Without `participants`, identify senders through each message's `sender_role`, `.env` UIDs or wizard assignments.

Multiple Afterglow JSONL files can be supplied at once:

```bash
uv run python -m xuwen.ingestion.cli import \
  chunk-001.jsonl chunk-002.jsonl \
  --plugin afterglow_v1 --language en
```

Multi-file import processes each file separately; it does not concatenate a logical JSONL stream first:

- Every typed JSONL file must contain its own `header`, followed only by `participant` and `message` records. Repeat the complete participants in each shard.
- Bare-message shards must independently meet the required-field and identity rules above.
- Keep `message.id` stable and globally unique across the export. Filename/line-number fallback IDs cannot guarantee idempotent deduplication after renaming, re-sharding or wizard re-upload.
- File boundaries are session-splitting boundaries. Windows and response pairs do not cross files; `conversation.id` does not currently merge shards.
- The wizard aggregates all task files for persona, style and circadian profiles. CLI batch import also rebuilds circadian data from all supplied files. After batch import, the lightweight proactive profile is rebuilt from all stored windows.

If chunks form one continuous conversation and cross-chunk context matters, the converter should sort, deduplicate and merge them into one self-contained typed JSONL before import.

These boundaries apply to text vectors. The independent persona aggregator deduplicates across files, groups by plugin, sorts each group by `(timestamp_ms, seq, message_id, sender_uid)` and re-splits sessions. Same-format chunk boundaries can therefore contribute persona reply samples. Different plugins contribute to word/circadian statistics but not cross-platform response pairs:

```bash
uv run python scripts/analyze_persona.py \
  chunk-001.jsonl chunk-002.jsonl
```

`scripts/analyze_persona.py` and `scripts/check_proactive_quality.py` are separate helper scripts without a `--language` option; some console messages remain Chinese. Do not append that option to them. Use the main CLI with `--language en` or `--language zh-CN` for imports and analysis that need an explicit language.

All profile inputs must concern the same target person. For multiple accounts/platforms, put all corresponding UIDs in `SELF_UID`/`FRIEND_UID`. Build separate profiles for different people.

`import-images` deduplicates image bytes by SHA-256 and saves originals to `.data/images/<sha>.<ext>`. Each unique hash calls `VISION_MODEL` once. It stores the description, timestamp, sender, context and `image_sha` in `history_images`. Retrieval normally uses these descriptions without resending original images on chat requests.
