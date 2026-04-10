# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

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

[Unreleased]: https://github.com/88hours/helix-community/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/88hours/helix-community/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/88hours/helix-community/releases/tag/v1.0.0
