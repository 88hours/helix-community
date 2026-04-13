# Helix
[![CircleCI](https://dl.circleci.com/status-badge/img/gh/88hours/helix-community/tree/main.svg?style=svg)](https://dl.circleci.com/status-badge/redirect/gh/88hours/helix-community/tree/main)
[![License](https://img.shields.io/badge/license-Apache%202.0%20%2B%20commercial%20restrictions-blue)](LICENSE)

Autonomous incident response — from production crash to fix suggestion, without waking anyone up.

Helix watches your error tracker. When a bug lands, it writes a failing test, runs a TDD loop to produce a passing fix, and opens a pull request. Your team approves in Slack before anything merges.

**Pipeline:** Crash Handler → QA Agent → Dev Agent → Notifier → Human Review

---

## How it works

1. **Crash Handler** — receives a Sentry or Rollbar webhook, extracts the crash context, and publishes a `crash_analysed` event
2. **QA Agent** — reads the crash report, writes a failing test case, opens a GitHub Issue, publishes `test_case_generated`
3. **Dev Agent** — clones the repo, writes the fix using a TDD loop (Claude Code CLI runs the failing test, iterates up to 3 times), commits, and opens a GitHub PR
4. **Notifier** — sends a Slack message with the PR link and crash context; escalates with full reasoning if the TDD loop exhausts all retries

---

## Quick start

```bash
git clone https://github.com/88hours/helix.git
cd helix
cp .env.example .env   # fill in your keys
docker compose up --build
```

## Claude Code

This repo ships a [Claude Code](https://claude.ai/code) skill that walks contributors through local setup interactively. If you have Claude Code installed, open the repo and run:

```
/setup
```

Claude will check your prerequisites, help you fill in `.env`, start the stack, and run the test suite — step by step.

Then point your Sentry or Rollbar webhook at `http://your-host:8000/webhook/sentry` (or `/webhook/rollbar`).

---

## Requirements

- Docker + Docker Compose
- An [Anthropic API key](https://console.anthropic.com/) **or** a locally running [Ollama](https://ollama.com/) instance
- A GitHub personal access token with `repo` scope
- A Sentry or Rollbar account (or both)
- A Slack bot with `chat:write` scope (strongly recommended)
- [Claude Code CLI](https://claude.ai/code) installed and authenticated (required for the Dev Agent TDD loop)

---

## Configuration

All configuration is via environment variables. Copy `.env.example` to `.env` and fill in your values.

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic only | Anthropic API key |
| `REDIS_URL` | Yes | Redis connection URL |
| `GITHUB_TOKEN` | Yes | GitHub PAT with repo scope |
| `HELIX_GITHUB_REPO` | Yes | Repo to fix bugs in (`owner/name`) |
| `SENTRY_WEBHOOK_SECRET` | One of these | Sentry client secret |
| `ROLLBAR_ACCESS_TOKEN` | One of these | Rollbar project read token |
| `SLACK_BOT_TOKEN` | Recommended | Bot token (`xoxb-...`) |
| `SLACK_SIGNING_SECRET` | Recommended | Slack app signing secret |
| `SLACK_APPROVAL_CHANNEL` | Recommended | Channel for Slack notifications |

### Model & provider

All agents share a single LLM configuration set in `config.yaml`. Two providers are supported:

**Anthropic (default)**
```bash
HELIX_PROVIDER=anthropic
HELIX_MODEL=claude-sonnet-4-6
```

**Ollama (local)**
```bash
HELIX_PROVIDER=ollama
HELIX_MODEL=llama3.2
HELIX_OLLAMA_BASE_URL=http://localhost:11434   # optional, this is the default
```

Ollama uses the standard OpenAI-compatible `/v1/chat/completions` endpoint — no API key required.

---

## What's included

- Sentry and Rollbar webhook ingestion
- Crash analysis and test case generation
- Full TDD loop: clone repo, run failing test, iterate fix with Claude Code CLI, open PR
- Fix suggestion posted to GitHub Issue as a comment on the first iteration
- Slack notifications with crash context and Issue link
- Self-hosted via Docker Compose

---

## Community vs Cloud

| Feature | Community (this repo) | Helix Cloud |
|---|---|---|
| All 4 agents | Yes | Yes |
| Sentry + Rollbar | Yes | Yes |
| GitHub Issues + fix suggestions | Yes | Yes |
| TDD loop (clone → test → fix → PR) | Yes | Yes |
| Automated PR creation | Yes | Yes |
| Slack notifications | Yes | Yes |
| Self-hosted | Yes | Yes |
| React dashboard | — | Yes |
| Auth0 / SSO | — | Yes |
| Per-project credentials | — | Yes |
| GitHub App (multi-repo) | — | Yes |
| OpenTelemetry tracing | — | Yes |
| LangSmith evals | — | Yes |
| Managed hosting | — | Yes |

---

## Supported languages

| Language | Test format |
|---|---|
| Python | pytest |
| JavaScript / TypeScript | Jest |
| Ruby | RSpec |
| Go | go test |
| Java / Kotlin | JUnit |

---

## Architecture

```
agents/
  crash_handler/   FastAPI app — receives webhooks, runs crash analysis
  qa/              Subscriber — generates test cases, opens GitHub Issues
  dev/             Subscriber — TDD loop: clone repo, run failing test, fix with Claude Code CLI, open PR
  notifier/        Subscriber — sends Slack notifications
core/
  config.py        Loads config.yaml + env var overrides
  events.py        Redis Pub/Sub publish/subscribe
  state.py         Redis read/write helpers, keyed by incident_id
  models.py        Pydantic models shared across agents
  llm.py           Anthropic / Ollama wrapper; `complete_tdd()` spawns Claude Code CLI subprocess
integrations/
  sentry.py        Sentry webhook parsing
  rollbar.py       Rollbar webhook parsing
  github.py        GitHub Issues
  slack.py         Slack messages
```

Agents communicate via Redis Pub/Sub events. State is stored in Redis, keyed by `incident_id`. No agent calls another agent directly.

---

## Running individual services

```bash
# Crash Handler only (webhook receiver)
docker compose up redis crash_handler

# Watch logs for a specific service
docker compose logs -f dev

# Run without Docker (for development)
pip install -e "."
uvicorn agents.crash_handler.main:app --reload   # crash handler
python -m agents.qa.main                          # QA agent
python -m agents.dev.main                         # Dev agent
python -m agents.notifier.main                    # Notifier
```

---

## Contact

General enquiries, bug reports, or feedback — [hello@88hours.io](mailto:hello@88hours.io)

Interested in Helix Cloud (managed hosting, dashboard, enterprise features)? [Talk to us](mailto:hello@88hours.io?subject=Helix%20Cloud%20enquiry).

---

## Licence

Helix is licensed under a modified Apache 2.0 licence with additional commercial restrictions.
Key points:

- **Personal and commercial self-hosted use** is permitted.
- **Multi-tenant deployments** (one workspace per tenant) require a commercial licence from 88hours.
- **Dashboard branding** (logo / copyright in `dashboard/` or `web/`) must not be removed.

See [LICENSE](LICENSE) for full terms. For commercial licensing, contact [hello@88hours.io](mailto:hello@88hours.io).
