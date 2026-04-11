---
name: setup
description: Interactive guide to set up Helix locally for development or contribution
user-invocable: true
---

Help the user set up Helix locally, step by step. Walk through each stage interactively, checking the environment before moving on.

## Setup stages

### 1. Prerequisites check

Check the following are available in the shell, and tell the user what to install if any are missing:
- `python3 --version` → must be 3.12+
- `docker --version` and `docker compose version` → required for full-stack mode
- `git --version`

If `uv` is available (`uv --version`), recommend it for dependency installation (faster). Otherwise fall back to pip.

### 2. Clone (if not already inside the repo)

If the current directory is not the helix-community repo:
```bash
git clone https://github.com/88hours/helix-community.git
cd helix-community
```

### 3. Install Python dependencies

With uv (recommended):
```bash
uv pip install -e ".[dev]"
```

With pip:
```bash
pip install -e ".[dev]"
```

### 4. Configure environment

```bash
cp .env.example .env
```

Walk the user through filling in `.env`. The required and optional variables are:

| Variable | Required | Notes |
|---|---|---|
| `REDIS_URL` | Yes | e.g. `redis://localhost:6379` — covered by Docker Compose |
| `GITHUB_TOKEN` | Yes | GitHub PAT with `repo` scope |
| `HELIX_GITHUB_REPO` | Yes | The repo Helix will post fixes to, e.g. `owner/repo` |
| `ANTHROPIC_API_KEY` | If using Anthropic | Get one at https://console.anthropic.com/ |
| `HELIX_PROVIDER` | No | `anthropic` (default) or `ollama` |
| `HELIX_MODEL` | No | e.g. `claude-sonnet-4-6` or `llama3.2` |
| `HELIX_OLLAMA_BASE_URL` | Ollama only | Default: `http://localhost:11434` |
| `SENTRY_WEBHOOK_SECRET` | One of these | Sentry client secret |
| `ROLLBAR_ACCESS_TOKEN` | One of these | Rollbar project read token |
| `SLACK_BOT_TOKEN` | Recommended | `xoxb-...` bot token |
| `SLACK_SIGNING_SECRET` | Recommended | Slack app signing secret |
| `SLACK_APPROVAL_CHANNEL` | Recommended | Channel name for notifications |

Ask the user which LLM provider they want to use (Anthropic or Ollama) and help them set the right variables for their choice.

### 5. Start the stack

**Option A — Full stack via Docker Compose (recommended for testing the full pipeline):**
```bash
docker compose up --build
```

**Option B — Individual agents without Docker (faster iteration on a single agent):**
```bash
# Terminal 1 — webhook receiver
uvicorn agents.crash_handler.main:app --reload

# Terminal 2 — test case generator
python -m agents.qa.main

# Terminal 3 — fix suggestion generator
python -m agents.dev.main

# Terminal 4 — Slack notifier
python -m agents.notifier.main
```

Ask the user which mode they prefer and show only the relevant commands.

### 6. Verify with tests

Run the test suite to confirm everything is wired up correctly (no real API keys needed — all HTTP calls are mocked):
```bash
pytest
```

Expected: 18 test files, all passing. If any fail, help diagnose.

### 7. Point a webhook (optional)

If the user wants to test the full end-to-end flow, tell them to point their Sentry or Rollbar webhook at:
- Sentry: `http://localhost:8000/webhook/sentry`
- Rollbar: `http://localhost:8000/webhook/rollbar`

---

## Tips to share

- The agents talk via Redis Pub/Sub — no agent calls another directly. If something seems stuck, check `docker compose logs -f <agent>`.
- `ruff check .` must pass before opening a PR. Run `ruff check . --fix && ruff format .` to auto-fix most issues.
- Tests live in `tests/` and mirror the source structure. Add a test file for any new module you create.
- For questions: hello@88hours.io
