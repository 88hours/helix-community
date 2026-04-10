# Contributing to Helix

Thank you for your interest in contributing to Helix!

## Before you start

By submitting a pull request, you agree that your contribution may be used under the same modified Apache 2.0 licence terms that govern this project (including the commercial use provisions in section 2 of [LICENSE](LICENSE)). If you are contributing on behalf of an employer, ensure you have the right to do so.

---

## Development setup

### Prerequisites

- Python 3.12+
- Docker + Docker Compose (for full-stack testing)
- Redis 7+ (or use the Docker Compose stack below)

### Install dependencies

Using [uv](https://github.com/astral-sh/uv) (recommended):

```bash
uv pip install -e ".[dev]"
```

Using standard pip:

```bash
pip install -e ".[dev]"
```

### Configure environment

```bash
cp .env.example .env
# Fill in at minimum:
#   REDIS_URL
#   GITHUB_TOKEN + HELIX_GITHUB_REPO
#   ANTHROPIC_API_KEY  (or set HELIX_PROVIDER=ollama and point at a local Ollama instance)
```

---

## Running the stack

**Full stack via Docker Compose** (recommended for end-to-end testing):

```bash
docker compose up --build
```

**Individual agents without Docker** (useful for iterating on a single agent):

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

---

## Running tests

```bash
pytest                       # all 18 test files
pytest tests/core/           # only core module tests
pytest tests/agents/         # only agent tests
pytest tests/integrations/   # only integration tests
pytest -k "test_sentry"      # filter by name
```

Tests mock all HTTP calls via `respx` and stub environment variables — no real API keys or Redis instance needed.

---

## Code style

[Ruff](https://docs.astral.sh/ruff/) handles both linting and import sorting.

```bash
ruff check .           # check for issues
ruff check . --fix     # auto-fix safe issues
ruff format .          # format code
```

Your PR should be clean (`ruff check .` exits 0) before review.

---

## Project structure

```
agents/
  crash_handler/   Webhook receiver (FastAPI) — Sentry & Rollbar
  qa/              Test-case generator — publishes test to GitHub Issue
  dev/             Fix suggester — posts suggestion to GitHub Issue
  notifier/        Slack notification sender
core/
  config.py        Configuration loader (YAML + env var overrides)
  models.py        Pydantic data models shared across agents
  events.py        Redis Pub/Sub helpers (publish / subscribe)
  state.py         Incident state in Redis (7-day TTL)
  llm.py           LLM router (Anthropic SDK or Ollama)
  utils.py         Shared utilities
integrations/
  github.py        GitHub REST API + git CLI wrappers
  slack.py         Slack API (messages, interactive buttons)
  sentry.py        Sentry webhook parsing + HMAC verification
  rollbar.py       Rollbar webhook parsing + token verification
  email.py         SMTP email notifications
tests/             Mirrors source structure — one test file per module
```

---

## How to submit a pull request

1. Fork the repository and create a feature branch from `main`.
2. Write or update tests for your change — all new behaviour should be covered.
3. Ensure `pytest` passes and `ruff check .` is clean.
4. Open a PR against `main`. Describe what changed and why.
5. A maintainer will review within a few business days.

Please keep PRs focused. One logical change per PR makes review easier.

---

## Reporting bugs

Open a [GitHub Issue](https://github.com/88hours/helix-community/issues). Include:

- Python version and OS
- LLM provider (Anthropic / Ollama + model name)
- Relevant agent logs (redact any API keys or tokens)
- Steps to reproduce

---

## Questions

[hello@88hours.io](mailto:hello@88hours.io)
