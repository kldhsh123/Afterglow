# Docker deployment and operations

Use Docker if you do not want to install Python, uv and backend dependencies. The standalone deployment stores `.env` and `.data/` in a host working directory so container upgrades preserve them.

```bash
mkdir -p ~/afterglow && cd ~/afterglow
curl -fsSLo compose.yaml \
  https://raw.githubusercontent.com/kldhsh123/Afterglow/main/docker/compose.standalone.yaml
# Create initial language and container-access settings; edit existing .env fields manually
if [ ! -f .env ]; then
  printf '%s\n' 'XUWEN_LANGUAGE=en' 'CONFIG_UI_LOCALHOST_ONLY=false' > .env
fi
docker compose pull
docker compose up -d
```

The first start detects missing configuration and enables the setup wizard. Find its session token:

```bash
docker compose logs backend | grep -iE "token|/config/"
```

Open `http://localhost:8000/config/`, paste the token into step 1 and complete all eight steps. The wizard writes `~/afterglow/.env` with backups under `.env-backups/`. Restart the backend to load the saved settings:

```bash
docker compose restart backend
```

The example creates a host `.env` containing `XUWEN_LANGUAGE=en` and `CONFIG_UI_LOCALHOST_ONLY=false` before the first start, enabling English output and wizard access from outside the container. If `.env` already exists, review those fields manually. English is also the default when unset. Use `XUWEN_LANGUAGE=zh-CN` for Chinese startup and proactive messages, then restart; the page's language dropdown is independent. Missing identity/model fields still enable setup.

Standalone Compose reads the mounted host `.env`. Prefixing the host command with `XUWEN_LANGUAGE=zh-CN docker compose up -d` alone does not pass the variable into the container. CLI commands can independently select output with `--language en` / `--language zh-CN`, as shown below.

After setup:

```text
~/afterglow/
├── compose.yaml
├── .env                     # Wizard configuration
├── .env.example             # Template copied from the image
├── .env-backups/            # Previous configurations
└── .data/
    ├── lancedb/             # Vector database
    ├── persona/             # Persona and state
    ├── stickers/            # Stickers
    ├── images/              # Image cache
    └── uploads/             # Temporary wizard uploads
```

### Routine commands

```bash
# Follow logs
docker compose logs -f backend

# Reload edited .env; settings do not watch it
docker compose restart backend

# Pull and recreate with the latest image
docker compose pull && docker compose up -d

# Put exports inside the mounted working directory
docker compose run --rm backend \
  python -m xuwen.ingestion.cli import .imports/qq.json --language en

# Optional historical images after text import
docker compose run --rm backend \
  python -m xuwen.ingestion.cli import-images .imports/export-dir --plugin afterglow_v1 --language en

# Build indexes for larger tables
docker compose run --rm backend python -m xuwen.ingestion.cli index --language en

# Stop; persistent data is retained
docker compose stop
docker compose down

# Inspect the running container
docker compose exec backend bash
```

### What about the frontend?

The image contains the backend and bundled setup wizard, not the main chat frontend. Run the chat frontend with `pnpm dev` as described in [Quick start](quickstart.md#start-the-frontend-optional), or host `frontend/dist` with Nginx. The backend exposes port 8000; route API requests there.

### Sharing data with a source deployment

Both modes can coexist, but avoid simultaneous processes on the same port. Their default directories differ: source instructions use repository `backend/.env` and `backend/.data/`; standalone Docker uses `~/afterglow/.env` and `~/afterglow/.data/`.

To share one configuration and database, start the source process from the Docker working directory:

```bash
cd ~/afterglow
XUWEN_LANGUAGE=en uv run --project /path/to/Afterglow/backend \
  uvicorn xuwen.chat_api.app:create_app --factory --reload

# After stopping that process, switch to Docker
docker compose up -d
```

Replace `/path/to/Afterglow` with your clone. Both then use the host working directory. Starting from the repository's `backend/` instead requires copying or migrating configuration and data yourself.

### Docker-specific notes

- On a cold first start, entrypoint writes `CONFIG_UI_LOCALHOST_ONLY=false` to the host `.env`. Container-external browsers arrive from a Docker bridge address and would otherwise be rejected. Tighten access after setup if appropriate for your network.
- Wizard writes use atomic rename into the host-mounted `.env`, so they survive container recreation.
- Entrypoint aligns the service user's uid/gid with the mount owner to avoid `EPERM` across WSL, Linux and NFS ownership differences.
- Restart after `.env` changes; `pydantic-settings` reads it at process start.

### Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| Wizard PUT `/config/values` returns 500 with EPERM | Mount ownership was not aligned successfully | Check entrypoint ownership-adjustment logs; report redacted logs if missing |
| Repeated LanceDB deprecation warnings | Upstream Lance warnings | `RUST_LOG=lance=error` suppresses these warnings |
