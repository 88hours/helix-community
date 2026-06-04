# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

---

## [1.4.0] — 2026-06-04

### Added
- **AWS Bedrock provider** — run Crash Handler and QA Agent via `anthropic.AnthropicBedrock`
  using an IAM role; no API key stored. Cross-region inference profiles are resolved
  automatically from the AWS region prefix (`us.`, `eu.`, `ap.`).
- **OpenRouter provider** — OpenAI-compatible API backend; set `OPENROUTER_API_KEY` and
  `provider: openrouter` per agent to use any model on OpenRouter's catalogue.
- **OpenCode and Goose CLI backends** — Dev Agent can now dispatch to the OpenCode or
  Goose CLI as alternatives to Claude Code.
- **Per-agent LLM configuration** — `config.yaml` now has an `agents:` block (replacing
  the global `llm:` block). Each agent has its own `provider` and `model`. Override at
  runtime with `HELIX_<AGENT>_PROVIDER` and `HELIX_<AGENT>_MODEL`.
- **`core/preflight.py`** — startup validation that checks at least one LLM provider key
  is set (or Bedrock is configured via IAM). Warns on missing optional env vars.
- **LangSmith tracing (opt-in)** — when `LANGSMITH_API_KEY` and `LANGSMITH_TRACING=true`
  are set, every `complete()` call is traced with provider, model, and token usage.
- `openai>=1.55` dependency for the OpenRouter and Ollama backends (replaces the previous
  httpx-based Ollama implementation).

### Changed
- `core/llm.py` — unified router now dispatches to seven backends: `anthropic`, `bedrock`,
  `openrouter`, `ollama`, `claude-code`, `opencode`, `goose`. Anthropic backend retries
  with `claude-haiku-4-5-20251001` on 529 Overloaded responses.
- `core/config.py` — `LLMConfig` / `get_llm_config()` replaced by `AgentConfig` /
  `get_agent_config(agent)`. Added `get_bedrock_region()`.
- `agents/dev/agent.py` — `complete_tdd(cwd=...)` replaced by `complete(agent="dev", cwd=...)`;
  provider is now determined by the `dev` agent's config entry.
- `config.yaml` — `llm:` block replaced by `agents:` block; `settings.aws_bedrock_region`
  added.

---

## [1.3.0] — 2026-04-19

### Added
- **Railway deployment** — one-click deploy button in README; `railway.toml`,
  `entrypoint.sh`, and `railway-deploy.sh` added so the full stack can be
  deployed to Railway without local Docker Compose.
- `entrypoint.sh` supports three modes: docker-compose `command:` args,
  `START_COMMAND` env var (per-service on Railway), or all-agents-in-one-container
  fallback for single-service deployments.

---

## [1.2.0] — 2026-04-16

### Security
- Upgraded `langsmith` to fix a token vulnerability in streams.

---

## [1.1.0] — 2026-04-09

### Added
- **Ollama support** — run Helix with a local LLM (llama3.2, Mistral,
  CodeLlama, etc.) via the OpenAI-compatible `/v1/chat/completions` endpoint.
  No API key required.
- `HELIX_PROVIDER`, `HELIX_MODEL`, and `HELIX_OLLAMA_BASE_URL` environment
  variables for runtime LLM selection.
- Ollama configuration section in README and `.env.example`.
- Community vs Cloud feature comparison table in README.
- Contact and Helix Cloud commercial enquiry links.

### Changed
- `core/llm.py` — unified LLM client supports both Anthropic SDK and Ollama
  (OpenAI-compatible) backends behind a single `complete()` function.
- `core/config.py` — `get_llm_config()` reads provider and model from
  environment variables with `config.yaml` as fallback.
- `config.yaml` — added `llm.ollama_base_url` field.

---

## [1.0.0] — 2026-04-09

Initial public release.

### Included
- **Crash Handler** — FastAPI webhook receiver for Sentry and Rollbar.
  HMAC-SHA256 signature verification for Sentry; access token comparison
  for Rollbar.
- **QA Agent** — generates failing test cases (pytest, Jest, RSpec, Go test,
  JUnit) from crash context and opens a GitHub Issue.
- **Dev Agent** — fetches relevant source files from the target repository;
  posts an LLM-generated fix suggestion as a GitHub Issue comment.
- **Notifier Agent** — sends a Slack notification with crash context, failing
  test, fix suggestion, and a link to the GitHub Issue.
- Redis Pub/Sub event bus for decoupled agent communication.
- Docker Compose setup for one-command local deployment.
- Anthropic (`claude-sonnet-4-6`) as the default LLM backend.
- 18 test files covering agents, core modules, and integrations.

---

[Unreleased]: https://github.com/88hours/helix-community/compare/v1.4.0...HEAD
[1.4.0]: https://github.com/88hours/helix-community/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/88hours/helix-community/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/88hours/helix-community/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/88hours/helix-community/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/88hours/helix-community/releases/tag/v1.0.0
