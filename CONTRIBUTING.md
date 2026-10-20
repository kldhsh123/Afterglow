[English](CONTRIBUTING.md) | [简体中文](readme/CONTRIBUTING.zh-CN.md) · [Development guide](https://kldhsh123.github.io/Afterglow/en/development.html) · [English docs](https://kldhsh123.github.io/Afterglow/en/)

# Contributing

Thank you for considering a contribution to Afterglow. This guide helps you prepare changes that are easy to review and merge.

If you only want to suggest an idea or report a bug, you do not need to read the whole guide. Open an [issue](https://github.com/kldhsh123/Afterglow/issues) using the appropriate template.

## Contribution agreement and code ownership

**By submitting a pull request, you agree to all terms in this section.** Please read them carefully.

By submitting a pull request (PR) or otherwise contributing code to this repository, you agree that:

- You retain copyright in your contributions.
- **You grant the repository owner (kldhsh123 and their designated successors or assignees) a perpetual, irrevocable, non-exclusive, worldwide, royalty-free right** to use, copy, modify, merge, publish, distribute, sublicense, and relicense your contributions in any form, including incorporating them into the project and **changing the license of the entire project**.
- This grant applies to any current or future license.
- Your contributions are distributed under the project's current license (AGPL-3.0) by default. The repository owner may change the license of the entire project at any time without obtaining further consent from you.
- If a particular contribution has additional restrictions, **state them explicitly in the PR description**. Otherwise, the grant is considered unrestricted.
- These terms form part of the Contributor License Agreement (CLA).

These terms allow Afterglow to evolve over the long term while respecting each contributor's work.

## Before you start

- Maintain user documentation equally in Chinese (`docs/wiki/`) and English (`docs/en/`). Issues, PRs, and commit messages may use either language; use English for variable names and identifiers. Add new user-facing messages through locale files. Existing Chinese comments do not need mechanical translation.
- Do not paste **real chat records** or API keys into issues or PRs. Redact screenshots and logs first, especially values such as `u_xxx`, `wxid_xxx`, `sk-xxx`, and `tvly-xxx`.
- Development happens on `dev`, with changes squashed into `main` for releases. **Target your PR at `dev`.**
- For substantial changes, such as a new model adapter, retrieval strategy, or prompt template, open an issue first to discuss the direction before implementing it.

## Intentional design boundaries

Before reporting an issue or preparing a fix, check whether it concerns either of the following intentional design decisions. Discuss changes to these boundaries before submitting a PR.

### The main chat pipeline fails fast

If a critical step in the main chat pipeline fails, the application stops the current request and returns a specific error. This is intentional behavior.

Afterglow prioritizes the integrity of each conversation pipeline and the quality of the final response. Continuing after a failure in retrieval, state handling, relationship memory, or prompt construction can leave context missing, put incorrect state into the prompt, or substantially reduce response quality. Fixes that swallow exceptions, inject empty results, or silently degrade the main request are therefore not accepted.

The main chat pipeline means the critical path from context preparation to final generation for online requests such as `/v1/chat/completions` and `/v1/responses`. Offline imports, directory scans, and other batch tasks may skip individual invalid files with warnings according to their own contracts.

### The project currently targets personal, single-user use

Afterglow is designed for personal use. It does not target simultaneous use by multiple users, tenant isolation, or high-concurrency services. Internal concurrency reduces the latency of individual requests; it does not imply support for multiple users sharing an instance and its state.

Issues solely about concurrent users, shared-state contention, tenant data isolation, or high-concurrency throughput are currently outside the project's scope and are not treated as valid defects. Concurrency isolation, thread safety, and capacity design may be revisited if the project's goals change.

## Finding something to work on

| Contribution | Where to start |
|---|---|
| Small bug fix or documentation typo | Submit a PR directly |
| Import plugin for another chat platform | [Import plugin development](docs/en/development.md#developing-import-plugins) |
| Configuration wizard UI | `backend/web_ui_src/`; see its [README](backend/web_ui_src/README.md) |
| Main chat frontend (时光信笺) | `frontend/`; see its [README](frontend/README.md) |
| New or updated prompt templates | `backend/xuwen/persona/templates/*.md.j2` |
| Retrieval or decision strategies | `backend/xuwen/memory/` and `backend/xuwen/companion/` |

## Development environment

To get started:

```bash
git clone https://github.com/<your-fork>/Afterglow.git
cd Afterglow
git remote add upstream https://github.com/kldhsh123/Afterglow.git

# Backend
cd backend
uv sync --extra dev

# Main chat frontend (optional; not required for the configuration wizard)
cd ../frontend
pnpm install

# Configuration UI source (only needed for wizard changes; npm is recommended on WSL)
cd ../backend/web_ui_src
npm install
```

For environment setup, models, and data imports, see [Quick start](docs/en/quickstart.md), [Configuration](docs/en/configuration.md), [Environment variables](docs/en/environment.md), and [Importing chats](docs/en/importing.md).

## Project structure for contributors

```text
Afterglow/
├── backend/
│   ├── xuwen/
│   │   ├── core/        # Domain models, errors, and time utilities
│   │   ├── ingestion/   # Parsing, cleaning, chunking, embeddings, plugin registry
│   │   ├── memory/      # LanceDB, retrieval fusion, and writeback
│   │   ├── persona/     # Offline profiles and Jinja2 prompt templates
│   │   ├── companion/   # Life timeline, relationship memory, interaction decisions
│   │   ├── chat_api/    # OpenAI-compatible FastAPI application
│   │   └── web_ui/      # Configuration wizard app and built assets
│   ├── web_ui_src/      # Wizard frontend; builds to ../xuwen/web_ui/static/
│   └── scripts/         # Offline scripts: import, analyze_persona, eval_retrieval
├── frontend/            # Vue 3 main chat UI (时光信笺), for testing and debugging
├── docs/                # Chinese/English sources for VitePress, GitHub Pages, and Wiki
└── .github/             # Issue templates and funding configuration
```

## Workflow

1. **Fork and clone**, then choose a descriptive branch name, such as `feat/import-discord`, `fix/embedding-dim-mismatch`, or `docs/api-cleanup`. Names such as `patch-1`, `update`, and `temp` are not accepted.
2. **Keep each PR focused on one thing.** Submit unrelated bug fixes in separate PRs.
3. **Run the checks before submitting** using the checklist below.
4. **Make the scope clear in the PR title.** Chinese and English are both welcome. `feat(ingestion): support Discord exports` is useful; `update` or `fixed some bugs` is not.
5. **Target `dev`.**

## Code conventions

### Python (backend)

- Python 3.12+.
- Use `ruff` for linting; configuration lives in `backend/pyproject.toml`.
- Use `mypy` with strict type checking (`strict = true`).
- Prefer `from __future__ import annotations`.
- Add type annotations, especially for public functions, models, and configuration.

```bash
cd backend
uv run ruff check xuwen scripts
uv run ruff format xuwen   # Optional; formatting is not mandatory
uv run mypy xuwen
```

If a new FastAPI route triggers B008 for a `Depends()` or `File()` default argument, add that file to `[tool.ruff.lint.per-file-ignores]` in `pyproject.toml`. **Do not add `# noqa` in the code.**

### TypeScript (frontend and configuration UI)

- The main chat frontend lives in `frontend/`; the configuration wizard lives in `backend/web_ui_src/`.
- Both use `vue-tsc` for type checking.
- There is no ESLint setup, but avoid obvious type escapes such as excessive `any` or `as unknown as` assertions.

```bash
cd frontend && npx vue-tsc --noEmit
cd backend/web_ui_src && npx vue-tsc --noEmit
```

### Comment and documentation language

- **Chinese is the primary language for code comments.** The repository originated as a Chinese-language project, so English-only comments in a PR can feel inconsistent.
- Avoid trivial comments such as `# add 1 here`. Explain why a decision was made.
- Function docstrings should explain intent rather than restating what the code does.

### Tests

Backend tests live in `backend/tests/`:

- `tests/unit/`: unit tests without external services.
- `tests/integration/`: API, import, and data-flow tests using temporary directories and mocked services.
- `tests/fixtures/`: small, synthetic fixtures maintained in the repository. Never include real chat records or credentials.

New features and bug fixes must include tests covering the new behavior or regression. Tests must not depend on a contributor's local `.env`, `.data/`, or live external APIs.

```bash
cd backend

# Run focused tests during development
uv run pytest tests/unit -q
uv run pytest tests/integration -q

# Run the full suite before submitting; CI uses the same command
uv run pytest tests -q
```

Pull requests trigger `.github/workflows/contributor-checks.yml`, which runs backend lint and the full test suite and builds the main chat frontend and configuration wizard. CI does not replace local checks. List the commands you actually ran and their results in the PR's Testing section. Documentation-only changes may state that tests are not applicable.

## Project-specific conventions

### 1. Never commit `.env` files

- `.gitignore` already excludes `backend/.env` and `backend/.data/`.
- PRs containing `.env` changes will be rejected immediately.
- Do not commit `.env` files generated by the configuration wizard.
- Do not commit the `backend/.env-backups/` directory.

### 2. Commit configuration wizard build output with its source

`backend/xuwen/web_ui/static/` is generated from `backend/web_ui_src/`. **Commit both together.** Otherwise users running the backend may receive an outdated UI that no longer matches the API.

```bash
cd backend/web_ui_src
npm install
npm run build      # Generates ../xuwen/web_ui/static/
cd ../..
git add backend/web_ui_src/ backend/xuwen/web_ui/static/
git commit -m "feat(config-ui): ..."
```

CI rebuilds the wizard and checks for uncommitted differences in `backend/xuwen/web_ui/static/`. It fails if source and output are out of sync. Reviewers should also confirm that both sets of changes belong to the same feature.

### 3. Never commit real chat files

The `开发缓存/` directory is ignored by Git and can hold your own exported JSON files. Do not put them elsewhere in the repository or paste excerpts into PR descriptions.

### 4. Preserve CLI behavior

The backend has two entry points:

- Configuration wizard: `uvicorn ...`, first-run mode, and the browser UI.
- CLI: `uv run python -m xuwen.ingestion.cli import ...`.

### 5. Preserve privacy redaction boundaries

The PII rules in `backend/xuwen/persona/redactor.py` deliberately redact phone numbers, email addresses, identity card numbers, bank card numbers, and IP addresses. QQ numbers, URLs, and domain names are intentionally retained. Open an issue before changing these boundaries because they directly affect which user data is sent to external APIs.

## Commit messages

Conventional Commits are recommended but not required:

```text
feat(ingestion): support Discord exports

- Add a discord_export plugin that detects webhook-exported JSON
- Reuse NormalizedMessage without changing the downstream pipeline

Closes #123
```

- Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, and `perf`.
- The optional scope commonly uses `ingestion`, `memory`, `chat-api`, `config-ui`, `frontend`, `persona`, `companion`, or `docs`.
- Keep the subject within 70 characters; put details in the body.
- Keep each commit focused on one thing. Split unrelated fixes and refactors into separate commits.

## PR checklist

Before submitting:

- [ ] The target branch is `dev`.
- [ ] `uv run ruff check xuwen scripts` passes.
- [ ] `uv run mypy xuwen` passes, or only existing warnings unrelated to the change remain.
- [ ] `uv run pytest tests -q` passes.
- [ ] Changes to `backend/web_ui_src/` include `npm run build` output in `static/`.
- [ ] Changes to `frontend/` have been checked with `npx vue-tsc --noEmit`.
- [ ] No `.env` files, `.data/` contents, or real chat JSON files are included.
- [ ] New features and bug fixes include relevant tests.
- [ ] New long-form documentation exists in both `docs/wiki/` and `docs/en/`; `docs/pages.json` and both locale navigation files are updated, and documentation checks and builds pass.
- [ ] The title and description explain what changed and why.
- [ ] The description includes `Closes #N` when the PR resolves an issue.

## Code review expectations

- The maintainer will respond on working days. Please be patient: this is a personally maintained project.
- Review comments may be direct and are about the code. Discussion is welcome when you disagree.
- Approved PRs are squash-merged into `dev`.

## Code of conduct

- Treat other contributors with basic respect.
- Comments involving personal attacks, gender, politics, ethnicity, or sexual orientation are not accepted.
- Take unrelated topics to the project's QQ group (`330316577`).
- Serious violations result in an immediate ban without a second warning.

## Acknowledgements

Every merged PR appears in the Git history and GitHub's contributor list. If your PR fixes an important bug or adds a major feature, feel free to request a mention in the release notes.

---

Afterglow exists to carry the warmth of words once shared into companionship ahead. We hope your contribution helps make that possible.

Thank you.
