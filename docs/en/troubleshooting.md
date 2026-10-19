# Troubleshooting

## Configuration changes have no effect

`.env` is read when the process starts. Stop and restart the backend; `uvicorn --reload` alone does not reload environment-file changes.

## The setup wizard does not appear

It is automatically enabled only when required settings are missing. Run the standalone entry:

```bash
cd backend
uv run python -m xuwen.web_ui --language en
```

Open `http://127.0.0.1:8765/config/` and use that process's console token. A setup token is different from the chat API key. If authorization fails after restarting, use the newly printed token.

## Embeddings return 400 or dimension errors

Check the OpenAI-compatible embedding base URL, `EMBEDDING_MODEL` and `EMBEDDING_DIM`. Do not include `/embeddings` in the base URL. Preserve a provider's required path such as `/v1`; Afterglow does not add it. Changing the model or dimension requires rebuilding old tables.

An HTML page returning HTTP 200 is not proof of a working model API. If `/v1` returns 503, inspect the upstream JSON error and provider configuration rather than removing `/v1` merely to reach a website.

## Import fails with `Spill has sent an error`

A typical error is `RuntimeError: lance error: LanceError(IO): Execution error: Spill has sent an error`. Despite the IO wording, it often indicates a mismatch between stored vector dimensions and `EMBEDDING_DIM`, for example when empty tables were created before a different model was selected. Reducing batches or concurrency does not fix a dimension mismatch.

The backend rebuilds mismatched empty tables at startup/import. For tables with data, import reports the mismatch before calling embeddings:

- To keep existing vectors, restore their original embedding model and dimension.
- To change models, stop the backend, back up and remove `LANCE_DB_PATH` (default `.data/lancedb`), restart, and import again. Vectors from different models cannot be mixed.

For Docker, after stopping requests and backing up the database, these commands apply **only** to the default `.data/lancedb` mounted at `/host-backend/.data/lancedb`:

```bash
docker exec afterglow-backend rm -rf /host-backend/.data/lancedb
docker restart afterglow-backend
```

Replace the removal path with the actual container path for custom `LANCE_DB_PATH`. This deletes the vector database, so keep the backup and original exports.

## Embeddings return 429

Reduce `EMBEDDING_MAX_CONCURRENCY` and set `EMBEDDING_MAX_REQUESTS_PER_MINUTE` to the provider's limit. Completed batches remain stored; rerun the same import after adjusting configuration.

## `ZoneInfoNotFoundError: No time zone found with key UTC`

Ensure `tzdata` is installed by syncing dependencies:

```bash
cd backend
uv sync --extra dev
```

Set a valid IANA `APP_TIMEZONE`, such as `Asia/Shanghai` or `Europe/London`.

## LanceDB write errors

Check free disk space and `.data` permissions first. For other large-write failures, temporarily reduce `EMBEDDING_BATCH_SIZE` and retry.

## Web search or URL reading returns no content

Check `WEB_ACCESS_ENABLED`, `WEB_FETCH_ENABLED`, provider URLs and keys. Web tools require an explicit request for public current information or an eligible URL; they are not automatically invoked every turn.

## Diagnostic commands

```bash
cd backend
uv run python -m xuwen.ingestion.cli stats --language en
curl http://127.0.0.1:8000/healthz
curl -H "Authorization: Bearer <XUWEN_API_KEY>" \
  -H "Accept-Language: en" http://127.0.0.1:8000/readyz
```

When filing an issue, include redacted logs, project version, OS, Python version and minimal reproduction steps. Never upload real chats, `.env` or API keys.
