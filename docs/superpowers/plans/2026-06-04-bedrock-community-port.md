# AWS Bedrock Integration Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the AWS Bedrock provider and per-agent config architecture from helix-server into helix-community, matching the implementation exactly.

**Architecture:** Replace helix-community's global `llm:` config with per-agent `agents:` config (matching helix-server). Add Bedrock plus all other providers (openrouter, opencode, goose) in `core/llm.py`. Add startup env validation via `core/preflight.py`. The `complete_tdd()` helper is removed — the dev agent calls `complete(agent="dev", cwd=...)` instead, and the router dispatches to `claude-code` based on config.

**Tech Stack:** Python 3.12, Anthropic SDK (`anthropic.AnthropicBedrock` for Bedrock), OpenAI SDK (openrouter + ollama backends), boto3 credential chain (IAM role, no stored keys for Bedrock).

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `pyproject.toml` | Modify | Add `openai>=1.55` dependency |
| `config.yaml` | Modify | Replace `llm:` block → `agents:` block + `settings: aws_bedrock_region` |
| `core/config.py` | Modify | Replace `LLMConfig`/`get_llm_config()` → `AgentConfig`/`get_agent_config()` + add `get_bedrock_region()` |
| `tests/core/test_config.py` | Modify | Update to cover new `AgentConfig`, `get_agent_config()`, `get_bedrock_region()` |
| `core/llm.py` | Modify | Add all providers (bedrock, openrouter, opencode, goose), update ollama to use openai SDK, update `complete()` signature, remove `complete_tdd()` |
| `tests/core/test_llm.py` | Modify | Update to cover all providers using `AgentConfig` |
| `agents/dev/agent.py` | Modify | Replace `complete_tdd(cwd=...)` → `complete(agent="dev", cwd=...)` |
| `core/preflight.py` | Create | Startup env validation: require at least one provider key (or Bedrock config) |

---

## Task 1: Add openai dependency to pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add openai to the dependencies list**

In `pyproject.toml`, add `"openai>=1.55",` after the boto3 line in the `dependencies` list:

```toml
    # LLM backends (all imported lazily — only the provider in use needs to be installed)
    "anthropic>=0.40",        # crash_handler, qa, code_quality agents
    "openai>=1.55",           # openrouter backend (OpenAI-compatible)
    "boto3>=1.35",            # eventbridge backend (optional; AWS deployments only)
    "langsmith>=0.7.31",         # LLM call tracing and eval suite
```

- [ ] **Step 2: Verify it parses cleanly**

```bash
cd /Users/nomi/Documents/88hours/engineering-wf/helix-community
python -c "import tomllib; tomllib.load(open('pyproject.toml','rb'))" && echo "OK"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
cd /Users/nomi/Documents/88hours/engineering-wf/helix-community
git add pyproject.toml
git commit -m "Deps: add openai>=1.55 for openrouter backend"
```

---

## Task 2: Restructure config.yaml

**Files:**
- Modify: `config.yaml`

- [ ] **Step 1: Replace the `llm:` block with a per-agent `agents:` block and add `settings:`**

Replace the entire `# LLM` section at the top of `config.yaml`. The new content for the LLM section:

```yaml
# ---------------------------------------------------------------------------
# LLM providers — one entry per agent
# ---------------------------------------------------------------------------
#
# provider options:
#   anthropic   — uses ANTHROPIC_API_KEY, direct Anthropic API
#   bedrock     — uses AWS IAM credentials (no API key stored), via AnthropicBedrock
#   openrouter  — uses OPENROUTER_API_KEY, OpenAI-compatible API
#   ollama      — OpenAI-compatible local inference (no API key required)
#                 base_url defaults to http://localhost:11434
#   claude-code — invokes the Claude Code CLI via subprocess (Dev Agent only)
#   opencode    — invokes the OpenCode CLI via subprocess (Dev Agent only)
#   goose       — invokes the Goose CLI via subprocess (Dev Agent only)
#
# Per-agent env var overrides:
#   HELIX_<AGENT>_PROVIDER   e.g. HELIX_CRASH_HANDLER_PROVIDER=bedrock
#   HELIX_<AGENT>_MODEL      e.g. HELIX_DEV_MODEL=claude-sonnet-4-6

agents:
  crash_handler:
    provider: anthropic
    model: claude-haiku-4-5-20251001

  qa:
    provider: anthropic
    model: claude-haiku-4-5-20251001

  dev:
    provider: claude-code
    model: claude-sonnet-4-6

  # Notifier has no LLM provider — it only sends notifications.
  notifier: {}
```

And add at the very bottom of `config.yaml`, before the `server:` block:

```yaml
# ---------------------------------------------------------------------------
# AWS settings
# ---------------------------------------------------------------------------
# Override: AWS_BEDROCK_REGION

settings:
  aws_bedrock_region: us-east-1
```

- [ ] **Step 2: Verify config.yaml parses**

```bash
cd /Users/nomi/Documents/88hours/engineering-wf/helix-community
python -c "import yaml; d=yaml.safe_load(open('config.yaml')); print(list(d.get('agents',{}).keys())); print(d.get('settings',{}))"
```

Expected output:
```
['crash_handler', 'qa', 'dev', 'notifier']
{'aws_bedrock_region': 'us-east-1'}
```

- [ ] **Step 3: Commit**

```bash
git add config.yaml
git commit -m "Config: replace global llm: block with per-agent agents: + add Bedrock region setting"
```

---

## Task 3: Refactor core/config.py

**Files:**
- Modify: `core/config.py`

The module-level docstring, `LLMConfig` dataclass, and `get_llm_config()` are replaced. `AgentConfig` and `get_agent_config(agent)` are added exactly as in helix-server. `get_bedrock_region()` is added. All other functions (`get_redis_url`, `get_rollbar_config`, `get_sentry_config`, `get_github_config`, `get_slack_config`) stay unchanged.

- [ ] **Step 1: Update the module docstring and imports**

Replace lines 1–31 of `core/config.py` (the docstring and imports section) with:

```python
"""
Configuration loader for Helix.

Reads settings from config.yaml and applies environment variable overrides.
No secrets are stored in config.yaml — only the names of the env vars that
hold them.

Agent model/provider overrides:
    HELIX_<AGENT>_PROVIDER   e.g. HELIX_DEV_PROVIDER=anthropic
    HELIX_<AGENT>_MODEL      e.g. HELIX_DEV_MODEL=claude-sonnet-4-6

Redis URL:
    REDIS_URL                e.g. redis://localhost:6379

Integration env vars:
    ROLLBAR_ACCESS_TOKEN     Rollbar project read token
    SENTRY_WEBHOOK_SECRET    Sentry client secret for HMAC verification
    GITHUB_TOKEN             GitHub personal access token (repo scope)
    SLACK_BOT_TOKEN          Slack bot token (xoxb-...)
    SLACK_APPROVAL_CHANNEL   Channel ID or name for approval messages
    SLACK_SIGNING_SECRET     Slack app signing secret

Usage:
    from core.config import get_agent_config, get_redis_url
    from core.config import get_rollbar_config, get_github_config, get_slack_config

    cfg = get_agent_config("dev")
    url = get_redis_url()
    gh  = get_github_config()
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml
```

- [ ] **Step 2: Replace `LLMConfig` dataclass with `AgentConfig`**

Remove the `LLMConfig` dataclass entirely and replace with `AgentConfig`:

```python
@dataclass
class AgentConfig:
    """Model and provider settings for a single agent."""
    agent: str                      # agent name as it appears in config.yaml, e.g. "dev"
    provider: str                   # "anthropic" | "bedrock" | "openrouter" | "ollama" | "claude-code" | "opencode" | "goose"
    model: str                      # model identifier, e.g. "claude-sonnet-4-6"
    base_url: Optional[str] = None  # required when provider = "ollama" (customer-provided)
```

Keep all other dataclasses (`RollbarConfig`, `SentryConfig`, `GitHubConfig`, `SlackConfig`) unchanged.

- [ ] **Step 3: Replace `get_llm_config()` with `get_agent_config(agent)` and add `get_bedrock_region()`**

Remove the entire `get_llm_config()` function and replace with:

```python
def get_agent_config(agent: str) -> AgentConfig:
    """
    Return the resolved AgentConfig for the given agent name.

    Resolution order (highest wins):
      1. Environment variable  HELIX_<AGENT>_PROVIDER / HELIX_<AGENT>_MODEL
      2. config.yaml entry for the agent

    Args:
        agent: Agent name matching a key under `agents:` in config.yaml,
               e.g. "crash_handler", "qa", "dev".

    Raises:
        FileNotFoundError: config.yaml does not exist.
        KeyError: The agent name is not defined in config.yaml.
        ValueError: The resolved provider or model is empty.
    """
    raw = _load_yaml()

    agents = raw.get("agents", {})
    if agent not in agents:
        available = ", ".join(agents.keys())
        raise KeyError(f"Agent '{agent}' not found in config.yaml. Available: {available}")

    agent_yaml = agents[agent] or {}
    env_prefix = f"HELIX_{agent.upper()}_"

    provider = os.environ.get(f"{env_prefix}PROVIDER") or agent_yaml.get("provider", "")
    model = os.environ.get(f"{env_prefix}MODEL") or agent_yaml.get("model", "")
    base_url = (
        os.environ.get(f"{env_prefix}BASE_URL")
        or os.environ.get("HELIX_OLLAMA_BASE_URL")
        or agent_yaml.get("base_url")
    )

    if not provider:
        raise ValueError(f"No provider configured for agent '{agent}'")
    if not model:
        raise ValueError(f"No model configured for agent '{agent}'")

    return AgentConfig(agent=agent, provider=provider, model=model, base_url=base_url)


def get_bedrock_region() -> str:
    """Return the AWS region for Bedrock calls. Reads AWS_BEDROCK_REGION env var, falls back to config.yaml settings.aws_bedrock_region, then us-east-1."""
    raw = _load_yaml()
    yaml_region = raw.get("settings", {}).get("aws_bedrock_region", "us-east-1")
    return os.environ.get("AWS_BEDROCK_REGION", yaml_region)
```

- [ ] **Step 4: Commit**

```bash
git add core/config.py
git commit -m "Config: replace LLMConfig/get_llm_config with AgentConfig/get_agent_config + add get_bedrock_region"
```

---

## Task 4: Update tests/core/test_config.py

**Files:**
- Modify: `tests/core/test_config.py`

- [ ] **Step 1: Rewrite the test file**

Replace the entire contents of `tests/core/test_config.py` with:

```python
"""Tests for core/config.py"""
import pytest
from unittest.mock import patch

from core.config import (
    get_agent_config,
    get_bedrock_region,
    get_redis_url,
    get_rollbar_config,
    get_sentry_config,
    get_github_config,
    get_slack_config,
)

SAMPLE_YAML = {
    "agents": {
        "crash_handler": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
        "qa": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
        "dev": {"provider": "claude-code", "model": "claude-sonnet-4-6"},
        "notifier": {},
    },
    "settings": {"aws_bedrock_region": "us-east-1"},
    "redis": {"url_env": "REDIS_URL"},
    "rollbar": {"access_token_env": "ROLLBAR_ACCESS_TOKEN"},
    "sentry": {"webhook_secret_env": "SENTRY_WEBHOOK_SECRET"},
    "github": {"target_repo": "acme/backend", "base_branch": "main", "token_env": "GITHUB_TOKEN"},
    "slack": {
        "token_env": "SLACK_BOT_TOKEN",
        "signing_secret_env": "SLACK_SIGNING_SECRET",
        "approval_channel_env": "SLACK_APPROVAL_CHANNEL",
    },
}


@pytest.fixture(autouse=True)
def mock_yaml():
    with patch("core.config._load_yaml", return_value=SAMPLE_YAML):
        yield


# ---------------------------------------------------------------------------
# get_agent_config
# ---------------------------------------------------------------------------

def test_get_agent_config_crash_handler():
    cfg = get_agent_config("crash_handler")
    assert cfg.agent == "crash_handler"
    assert cfg.provider == "anthropic"
    assert cfg.model == "claude-haiku-4-5-20251001"
    assert cfg.base_url is None


def test_get_agent_config_dev():
    cfg = get_agent_config("dev")
    assert cfg.provider == "claude-code"
    assert cfg.model == "claude-sonnet-4-6"


def test_get_agent_config_env_provider_override(monkeypatch):
    monkeypatch.setenv("HELIX_CRASH_HANDLER_PROVIDER", "bedrock")
    cfg = get_agent_config("crash_handler")
    assert cfg.provider == "bedrock"


def test_get_agent_config_env_model_override(monkeypatch):
    monkeypatch.setenv("HELIX_QA_MODEL", "claude-sonnet-4-6")
    cfg = get_agent_config("qa")
    assert cfg.model == "claude-sonnet-4-6"


def test_get_agent_config_unknown_agent_raises():
    with pytest.raises(KeyError, match="not found"):
        get_agent_config("nonexistent")


def test_get_agent_config_notifier_no_provider_raises():
    with pytest.raises(ValueError, match="No provider configured"):
        get_agent_config("notifier")


# ---------------------------------------------------------------------------
# get_bedrock_region
# ---------------------------------------------------------------------------

def test_get_bedrock_region_from_yaml():
    assert get_bedrock_region() == "us-east-1"


def test_get_bedrock_region_env_override(monkeypatch):
    monkeypatch.setenv("AWS_BEDROCK_REGION", "eu-west-1")
    assert get_bedrock_region() == "eu-west-1"


def test_get_bedrock_region_yaml_fallback():
    yaml_with_region = {**SAMPLE_YAML, "settings": {"aws_bedrock_region": "ap-southeast-1"}}
    with patch("core.config._load_yaml", return_value=yaml_with_region):
        assert get_bedrock_region() == "ap-southeast-1"


# ---------------------------------------------------------------------------
# get_redis_url
# ---------------------------------------------------------------------------

def test_get_redis_url(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    assert get_redis_url() == "redis://localhost:6379"


def test_get_redis_url_missing_raises(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    with pytest.raises(EnvironmentError, match="REDIS_URL"):
        get_redis_url()


# ---------------------------------------------------------------------------
# get_rollbar_config
# ---------------------------------------------------------------------------

def test_get_rollbar_config(monkeypatch):
    monkeypatch.setenv("ROLLBAR_ACCESS_TOKEN", "my-token")
    cfg = get_rollbar_config()
    assert cfg.access_token == "my-token"


# ---------------------------------------------------------------------------
# get_sentry_config
# ---------------------------------------------------------------------------

def test_get_sentry_config(monkeypatch):
    monkeypatch.setenv("SENTRY_WEBHOOK_SECRET", "sentry-secret")
    cfg = get_sentry_config()
    assert cfg.webhook_secret == "sentry-secret"


def test_get_sentry_config_missing_returns_none(monkeypatch):
    monkeypatch.delenv("SENTRY_WEBHOOK_SECRET", raising=False)
    cfg = get_sentry_config()
    assert cfg.webhook_secret is None


# ---------------------------------------------------------------------------
# get_github_config
# ---------------------------------------------------------------------------

def test_get_github_config(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    cfg = get_github_config()
    assert cfg.target_repo == "acme/backend"
    assert cfg.base_branch == "main"
    assert cfg.token == "ghp_test"


def test_get_github_config_env_repo_override(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("HELIX_GITHUB_REPO", "other-org/other-repo")
    cfg = get_github_config()
    assert cfg.target_repo == "other-org/other-repo"


def test_get_github_config_missing_repo_raises(monkeypatch):
    monkeypatch.delenv("HELIX_GITHUB_REPO", raising=False)
    yaml_no_repo = {**SAMPLE_YAML, "github": {"base_branch": "main", "token_env": "GITHUB_TOKEN"}}
    with patch("core.config._load_yaml", return_value=yaml_no_repo):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
        with pytest.raises(ValueError, match="target_repo"):
            get_github_config()


# ---------------------------------------------------------------------------
# get_slack_config
# ---------------------------------------------------------------------------

def test_get_slack_config(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "signing-secret")
    monkeypatch.setenv("SLACK_APPROVAL_CHANNEL", "C123")
    cfg = get_slack_config()
    assert cfg.token == "xoxb-test"
    assert cfg.signing_secret == "signing-secret"
    assert cfg.approval_channel == "C123"


def test_get_slack_config_missing_vars_returns_none(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("SLACK_APPROVAL_CHANNEL", raising=False)
    cfg = get_slack_config()
    assert cfg.token is None
    assert cfg.signing_secret is None
    assert cfg.approval_channel is None
```

- [ ] **Step 2: Run the tests — expect failures until implementation is done**

```bash
cd /Users/nomi/Documents/88hours/engineering-wf/helix-community
python -m pytest tests/core/test_config.py -v 2>&1 | head -40
```

Expected: tests for `get_agent_config`, `get_bedrock_region` PASS (Task 3 was done first). Tests that previously used `get_llm_config` are gone.

- [ ] **Step 3: Run full config test suite**

```bash
python -m pytest tests/core/test_config.py -v
```

Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/core/test_config.py
git commit -m "Tests: update test_config.py for AgentConfig/get_agent_config + get_bedrock_region"
```

---

## Task 5: Rewrite core/llm.py

**Files:**
- Modify: `core/llm.py`

Replace the entire file. Key changes vs current community version:
- Import `AgentConfig`, `get_agent_config` instead of `LLMConfig`, `get_llm_config`
- Add `_complete_bedrock()`, `_complete_openrouter()`, `_complete_opencode()`, `_complete_goose()`
- Update `_complete_anthropic()` to handle 529 overloaded errors (haiku fallback)
- Replace `_complete_ollama()` with the openai-SDK-based version (matching helix-server)
- Update `_complete_claude_code()` to return `tuple[str, dict]`
- Update `complete()` to accept `cwd` param, route all 7 providers
- Remove `complete_tdd()` — dev agent uses `complete(agent="dev", cwd=...)`
- Add LangSmith graceful import and tracing (langsmith is already in deps)

- [ ] **Step 1: Write the new core/llm.py**

Replace the entire contents of `core/llm.py` with:

```python
"""
LLM router for the Helix agent pipeline.

Routes completion requests to the correct backend based on the agent's
configuration in config.yaml (or env var overrides):

    anthropic    — Anthropic SDK, direct API calls (ANTHROPIC_API_KEY)
    bedrock      — Anthropic SDK via AWS Bedrock (IAM role, no stored key)
    openrouter   — OpenAI-compatible SDK via OpenRouter (OPENROUTER_API_KEY)
    ollama       — OpenAI-compatible local inference; no API key needed
    claude-code  — Claude Code CLI via subprocess: claude -p "<prompt>"
                   Used exclusively by the Dev Agent, which runs inside a
                   cloned repo directory so Claude Code has full file access.
    opencode     — OpenCode CLI via subprocess: opencode run "<prompt>"
    goose        — Goose CLI via subprocess: goose run --text "<prompt>"

All agents call the same function:

    response = await complete(agent="crash_handler", prompt="...", system="...")

The Dev Agent additionally passes cwd= so the subprocess runs inside the repo:

    response = await complete(agent="dev", prompt="...", cwd="/tmp/repo-abc123")

LangSmith tracing:
    When LANGSMITH_API_KEY and LANGSMITH_TRACING=true are set, every call to
    complete() is traced automatically — inputs (agent, prompt, system), output
    (response text), token usage, provider, and model are recorded in LangSmith.
"""

import asyncio
import logging
import os
from typing import Optional

from core.config import AgentConfig, get_agent_config

# LangSmith tracing — gracefully disabled when the package is not installed
# or LANGSMITH_TRACING is not set.
try:
    from langsmith import traceable as _langsmith_traceable
    from langsmith.run_helpers import get_current_run_tree as _get_run_tree
except ImportError:
    def _langsmith_traceable(**kwargs):  # type: ignore[misc]
        """No-op decorator used when langsmith is not installed."""
        def decorator(fn):
            return fn
        return decorator

    def _get_run_tree():  # type: ignore[misc]
        """Returns None when langsmith is not installed."""
        return None

logger = logging.getLogger(__name__)

_MAX_TOKENS = 8192

# Timeout in seconds for the claude-code / opencode subprocess.
_SUBPROCESS_TIMEOUT = 600
# Goose runs local models which are slower to start but shorter per-iteration.
_GOOSE_TIMEOUT = 120

# Fallback model used when Anthropic returns 529 Overloaded for the primary model.
_HAIKU_FALLBACK = "claude-haiku-4-5-20251001"

# Bedrock model ID mapping from Helix internal names to Bedrock ARN base IDs.
_BEDROCK_MODEL_IDS = {
    "claude-haiku-4-5":  "anthropic.claude-haiku-4-5-20251001-v1:0",
    "claude-sonnet-4-6": "anthropic.claude-sonnet-4-6-20250514-v1:0",
    "claude-opus-4-6":   "anthropic.claude-opus-4-6-20250514-v1:0",
}
# Cross-region inference profile prefix by AWS region prefix.
_BEDROCK_REGION_PREFIXES = {"us": "us.", "eu": "eu.", "ap": "ap."}


# ---------------------------------------------------------------------------
# Bedrock backend
# ---------------------------------------------------------------------------

def _bedrock_model_id(model: str, region: str) -> str:
    base = _BEDROCK_MODEL_IDS.get(model, model)
    prefix = _BEDROCK_REGION_PREFIXES.get(region.split("-")[0], "")
    return prefix + base


def _get_bedrock_client(region: str):
    import anthropic
    return anthropic.AnthropicBedrock(aws_region=region)


async def _complete_bedrock(
    config: AgentConfig, prompt: str, system: str
) -> tuple[str, dict]:
    from core.config import get_bedrock_region
    region = get_bedrock_region()
    bedrock_model = _bedrock_model_id(config.model, region)
    client = _get_bedrock_client(region)
    kwargs: dict = {
        "model": bedrock_model,
        "max_tokens": _MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    response = await asyncio.to_thread(client.messages.create, **kwargs)
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    return response.content[0].text, usage


# ---------------------------------------------------------------------------
# Anthropic backend
# ---------------------------------------------------------------------------

async def _complete_anthropic(
    config: AgentConfig, prompt: str, system: str
) -> tuple[str, dict]:
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY is not set")

    client = anthropic.AsyncAnthropic(api_key=api_key)
    kwargs: dict = {
        "model": config.model,
        "max_tokens": _MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system

    try:
        message = await client.messages.create(**kwargs)
    except anthropic.APIStatusError as exc:
        if exc.status_code == 529:
            logger.warning(
                "Anthropic model overloaded (529) — retrying with %s",
                _HAIKU_FALLBACK,
                extra={"original_model": config.model},
            )
            kwargs["model"] = _HAIKU_FALLBACK
            message = await client.messages.create(**kwargs)
        else:
            raise

    usage = {
        "input_tokens": message.usage.input_tokens,
        "output_tokens": message.usage.output_tokens,
    }
    return message.content[0].text, usage


# ---------------------------------------------------------------------------
# OpenRouter backend
# ---------------------------------------------------------------------------

async def _complete_openrouter(
    config: AgentConfig, prompt: str, system: str
) -> tuple[str, dict]:
    import openai

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise EnvironmentError("OPENROUTER_API_KEY is not set")

    client = openai.AsyncOpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = await client.chat.completions.create(
        model=config.model,
        messages=messages,
        max_tokens=_MAX_TOKENS,
    )
    usage = {
        "input_tokens": response.usage.prompt_tokens,
        "output_tokens": response.usage.completion_tokens,
    }
    return response.choices[0].message.content, usage


# ---------------------------------------------------------------------------
# Ollama backend (OpenAI-compatible /v1/chat/completions endpoint)
# ---------------------------------------------------------------------------

async def _complete_ollama(
    config: AgentConfig, prompt: str, system: str
) -> tuple[str, dict]:
    import openai

    base_url = config.base_url or os.environ.get("HELIX_OLLAMA_BASE_URL", "http://localhost:11434/v1")

    client = openai.AsyncOpenAI(
        base_url=base_url,
        api_key="ollama",  # Ollama ignores the key; placeholder required by the SDK
    )

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = await client.chat.completions.create(
        model=config.model,
        messages=messages,
        max_tokens=_MAX_TOKENS,
    )
    usage = {
        "input_tokens": response.usage.prompt_tokens if response.usage else 0,
        "output_tokens": response.usage.completion_tokens if response.usage else 0,
    }
    return response.choices[0].message.content, usage


# ---------------------------------------------------------------------------
# OpenCode CLI backend
# ---------------------------------------------------------------------------

async def _complete_opencode(prompt: str, cwd: Optional[str]) -> tuple[str, dict]:
    cmd = ["opencode", "run", "--dangerously-skip-permissions"]
    model = os.environ.get("HELIX_DEV_OPENCODE_MODEL")
    if model:
        cmd += ["--model", model]
    cmd.append(prompt)

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    logger.info("opencode subprocess started", extra={"pid": process.pid, "cwd": cwd})

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=_SUBPROCESS_TIMEOUT,
        )
    except asyncio.TimeoutError:
        process.kill()
        raise asyncio.TimeoutError(
            f"opencode subprocess timed out after {_SUBPROCESS_TIMEOUT}s"
        )

    stderr_text = stderr.decode().strip()
    stdout_text = stdout.decode().strip()

    if process.returncode != 0:
        raise RuntimeError(
            f"opencode exited with code {process.returncode}: {stderr_text}"
        )

    return stdout_text, {}


# ---------------------------------------------------------------------------
# Goose CLI backend
# ---------------------------------------------------------------------------

async def _complete_goose(prompt: str, cwd: Optional[str], system: str = "") -> tuple[str, dict]:
    cmd = ["goose", "run", "--no-session"]
    model = os.environ.get("HELIX_DEV_GOOSE_MODEL")
    if model:
        bare_model = model.split("/", 1)[-1]
        cmd += ["--model", bare_model]
    system_prompt = system or os.environ.get("HELIX_DEV_GOOSE_SYSTEM", "")
    if cwd:
        system_prompt = (system_prompt + f"\nYour working directory is: {cwd}. Do not leave this directory.").strip()
    if system_prompt:
        cmd += ["--system", system_prompt]
    cmd += ["--text", prompt]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    logger.info("goose subprocess started", extra={"pid": process.pid, "cwd": cwd})

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=_GOOSE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        process.kill()
        raise asyncio.TimeoutError(
            f"goose subprocess timed out after {_GOOSE_TIMEOUT}s"
        )

    stderr_text = stderr.decode().strip()
    stdout_text = stdout.decode().strip()

    if process.returncode != 0:
        raise RuntimeError(
            f"goose exited with code {process.returncode}: {stderr_text or stdout_text}"
        )

    if "Network error:" in stdout_text or "Could not connect" in stdout_text:
        raise RuntimeError(f"goose network error: {stdout_text}")

    return stdout_text, {}


# ---------------------------------------------------------------------------
# Claude Code CLI backend
# ---------------------------------------------------------------------------

async def _complete_claude_code(prompt: str, cwd: Optional[str]) -> tuple[str, dict]:
    process = await asyncio.create_subprocess_exec(
        "claude", "--dangerously-skip-permissions", "-p", prompt,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    logger.info("claude-code subprocess started", extra={"pid": process.pid, "cwd": cwd})

    async def _stream_stderr(stream: asyncio.StreamReader) -> str:
        lines: list[str] = []
        async for raw in stream:
            line = raw.decode().rstrip()
            lines.append(line)
            logger.info("claude-code | %s", line)
        return "\n".join(lines)

    async def _stream_stdout(stream: asyncio.StreamReader) -> str:
        lines: list[str] = []
        async for raw in stream:
            line = raw.decode().rstrip()
            lines.append(line)
            logger.info("claude-code out | %s", line)
        return "\n".join(lines).strip()

    try:
        stderr_text, stdout_text = await asyncio.wait_for(
            asyncio.gather(
                _stream_stderr(process.stderr),
                _stream_stdout(process.stdout),
            ),
            timeout=_SUBPROCESS_TIMEOUT,
        )
        await process.wait()
    except asyncio.TimeoutError:
        process.kill()
        raise asyncio.TimeoutError(
            f"claude-code subprocess timed out after {_SUBPROCESS_TIMEOUT}s"
        )

    logger.info(
        "claude-code subprocess finished",
        extra={"pid": process.pid, "returncode": process.returncode},
    )

    if process.returncode != 0:
        raise RuntimeError(
            f"claude-code exited with code {process.returncode}: {stderr_text}"
        )

    return stdout_text, {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@_langsmith_traceable(run_type="llm", name="helix_complete")
async def complete(
    agent: str,
    prompt: str,
    system: str = "",
    cwd: Optional[str] = None,
    config: Optional[AgentConfig] = None,
) -> str:
    """
    Run a completion for the given agent, routed to the correct LLM backend.

    Args:
        agent:  Agent name as defined in config.yaml, e.g. "crash_handler", "qa", "dev".
        prompt: The main prompt / user message.
        system: Optional system prompt.
        cwd:    Working directory for the claude-code/opencode/goose subprocess.
                Only relevant when the agent's provider is a CLI backend.
        config: Optional pre-resolved AgentConfig. When provided, skips the
                global config lookup.

    Returns:
        The model's text response as a plain string.

    Raises:
        KeyError:          Agent not found in config.yaml.
        EnvironmentError:  Required API key not set.
        RuntimeError:      CLI subprocess failed.
        ValueError:        Unknown provider in config.
    """
    resolved: AgentConfig = config or get_agent_config(agent)
    logger.info(
        "llm call",
        extra={"agent": agent, "provider": resolved.provider, "model": resolved.model},
    )

    if resolved.provider == "anthropic":
        response, usage = await _complete_anthropic(resolved, prompt, system)
    elif resolved.provider == "bedrock":
        response, usage = await _complete_bedrock(resolved, prompt, system)
    elif resolved.provider == "openrouter":
        response, usage = await _complete_openrouter(resolved, prompt, system)
    elif resolved.provider == "ollama":
        response, usage = await _complete_ollama(resolved, prompt, system)
    elif resolved.provider == "claude-code":
        response, usage = await _complete_claude_code(prompt, cwd)
    elif resolved.provider == "opencode":
        response, usage = await _complete_opencode(prompt, cwd)
    elif resolved.provider == "goose":
        response, usage = await _complete_goose(prompt, cwd, system=system)
    else:
        raise ValueError(
            f"Unknown provider '{resolved.provider}' for agent '{agent}'. "
            "Must be 'anthropic', 'bedrock', 'openrouter', 'ollama', 'claude-code', 'opencode', or 'goose'."
        )

    try:
        rt = _get_run_tree()
        if rt is not None:
            rt.add_metadata({
                "provider": resolved.provider,
                "model": resolved.model,
                **usage,
            })
    except Exception:
        pass

    return response
```

- [ ] **Step 2: Commit**

```bash
git add core/llm.py
git commit -m "LLM: add Bedrock provider, openrouter, opencode, goose; per-agent config; remove complete_tdd"
```

---

## Task 6: Update tests/core/test_llm.py

**Files:**
- Modify: `tests/core/test_llm.py`

- [ ] **Step 1: Rewrite the test file**

Replace the entire contents of `tests/core/test_llm.py` with:

```python
"""Tests for core/llm.py"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.config import AgentConfig
from core.llm import complete, _bedrock_model_id


def _make_config(provider: str, model: str = "claude-test", base_url: str = None) -> AgentConfig:
    return AgentConfig(agent="crash_handler", provider=provider, model=model, base_url=base_url)


# ---------------------------------------------------------------------------
# Anthropic backend
# ---------------------------------------------------------------------------

async def test_complete_anthropic(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="analysis result")]
    mock_msg.usage = MagicMock(input_tokens=100, output_tokens=50)
    mock_client = AsyncMock()
    mock_client.messages.create.return_value = mock_msg

    with patch("core.llm.get_agent_config", return_value=_make_config("anthropic")):
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await complete("crash_handler", "analyze this", system="you are an expert")

    assert result == "analysis result"


async def test_complete_anthropic_missing_key_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with patch("core.llm.get_agent_config", return_value=_make_config("anthropic")):
        with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
            await complete("crash_handler", "prompt")


async def test_complete_anthropic_no_system(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="result")]
    mock_msg.usage = MagicMock(input_tokens=50, output_tokens=20)
    mock_client = AsyncMock()
    mock_client.messages.create.return_value = mock_msg

    with patch("core.llm.get_agent_config", return_value=_make_config("anthropic")):
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await complete("crash_handler", "prompt")

    assert result == "result"
    call_kwargs = mock_client.messages.create.call_args[1]
    assert "system" not in call_kwargs


async def test_complete_anthropic_529_retries_with_haiku(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    import anthropic as _anthropic
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="haiku fallback result")]
    mock_msg.usage = MagicMock(input_tokens=50, output_tokens=20)
    mock_client = AsyncMock()
    overload_exc = _anthropic.APIStatusError(
        "overloaded", response=MagicMock(status_code=529), body={}
    )
    mock_client.messages.create.side_effect = [overload_exc, mock_msg]

    with patch("core.llm.get_agent_config", return_value=_make_config("anthropic", "claude-sonnet-4-6")):
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await complete("crash_handler", "prompt")

    assert result == "haiku fallback result"
    assert mock_client.messages.create.call_count == 2
    second_call_kwargs = mock_client.messages.create.call_args_list[1][1]
    assert second_call_kwargs["model"] == "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# Bedrock backend
# ---------------------------------------------------------------------------

def test_bedrock_model_id_us_region():
    result = _bedrock_model_id("claude-haiku-4-5", "us-east-1")
    assert result == "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def test_bedrock_model_id_eu_region():
    result = _bedrock_model_id("claude-sonnet-4-6", "eu-west-1")
    assert result == "eu.anthropic.claude-sonnet-4-6-20250514-v1:0"


def test_bedrock_model_id_ap_region():
    result = _bedrock_model_id("claude-opus-4-6", "ap-southeast-1")
    assert result == "ap.anthropic.claude-opus-4-6-20250514-v1:0"


def test_bedrock_model_id_unknown_model_passes_through():
    result = _bedrock_model_id("custom-model-id", "us-east-1")
    assert result == "us.custom-model-id"


async def test_complete_bedrock():
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="bedrock result")]
    mock_response.usage = MagicMock(input_tokens=80, output_tokens=40)
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    with patch("core.llm.get_agent_config", return_value=_make_config("bedrock", "claude-haiku-4-5")):
        with patch("core.llm.get_bedrock_region", return_value="us-east-1"):
            with patch("core.llm._get_bedrock_client", return_value=mock_client):
                with patch("asyncio.to_thread", new=AsyncMock(return_value=mock_response)):
                    result = await complete("crash_handler", "analyze this", system="you are an expert")

    assert result == "bedrock result"


async def test_complete_bedrock_no_system():
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="bedrock no-system result")]
    mock_response.usage = MagicMock(input_tokens=60, output_tokens=30)

    with patch("core.llm.get_agent_config", return_value=_make_config("bedrock", "claude-haiku-4-5")):
        with patch("core.llm.get_bedrock_region", return_value="us-east-1"):
            with patch("core.llm._get_bedrock_client", return_value=MagicMock()):
                with patch("asyncio.to_thread", new=AsyncMock(return_value=mock_response)):
                    result = await complete("crash_handler", "prompt")

    assert result == "bedrock no-system result"


# ---------------------------------------------------------------------------
# OpenRouter backend
# ---------------------------------------------------------------------------

async def test_complete_openrouter(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="openrouter result"))]
    mock_response.usage = MagicMock(prompt_tokens=70, completion_tokens=35)
    mock_client = AsyncMock()
    mock_client.chat.completions.create.return_value = mock_response

    with patch("core.llm.get_agent_config", return_value=_make_config("openrouter", "anthropic/claude-haiku")):
        with patch("openai.AsyncOpenAI", return_value=mock_client):
            result = await complete("crash_handler", "analyze this", system="system prompt")

    assert result == "openrouter result"


async def test_complete_openrouter_missing_key_raises(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with patch("core.llm.get_agent_config", return_value=_make_config("openrouter")):
        with pytest.raises(EnvironmentError, match="OPENROUTER_API_KEY"):
            await complete("crash_handler", "prompt")


# ---------------------------------------------------------------------------
# Ollama backend
# ---------------------------------------------------------------------------

async def test_complete_ollama():
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="ollama result"))]
    mock_response.usage = MagicMock(prompt_tokens=80, completion_tokens=30)
    mock_client = AsyncMock()
    mock_client.chat.completions.create.return_value = mock_response

    with patch("core.llm.get_agent_config", return_value=_make_config("ollama", "llama3.2")):
        with patch("openai.AsyncOpenAI", return_value=mock_client):
            result = await complete("qa", "prompt", system="sys")

    assert result == "ollama result"


async def test_complete_ollama_no_system():
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="ollama no-system result"))]
    mock_response.usage = MagicMock(prompt_tokens=60, completion_tokens=20)
    mock_client = AsyncMock()
    mock_client.chat.completions.create.return_value = mock_response

    with patch("core.llm.get_agent_config", return_value=_make_config("ollama", "llama3.2")):
        with patch("openai.AsyncOpenAI", return_value=mock_client):
            result = await complete("qa", "prompt")

    assert result == "ollama no-system result"
    messages = mock_client.chat.completions.create.call_args[1]["messages"]
    assert not any(m["role"] == "system" for m in messages)


# ---------------------------------------------------------------------------
# Unknown provider
# ---------------------------------------------------------------------------

async def test_complete_unknown_provider_raises():
    with patch("core.llm.get_agent_config", return_value=_make_config("unknown-provider")):
        with pytest.raises(ValueError, match="Unknown provider"):
            await complete("crash_handler", "prompt")
```

- [ ] **Step 2: Run the tests**

```bash
cd /Users/nomi/Documents/88hours/engineering-wf/helix-community
python -m pytest tests/core/test_llm.py -v
```

Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/core/test_llm.py
git commit -m "Tests: update test_llm.py for AgentConfig, all providers, Bedrock model ID mapping"
```

---

## Task 7: Update agents/dev/agent.py

**Files:**
- Modify: `agents/dev/agent.py`

`complete_tdd()` no longer exists. The dev agent calls `complete(agent="dev", cwd=...)` instead, and the router dispatches to the `claude-code` backend based on config.

- [ ] **Step 1: Update the import line**

In `agents/dev/agent.py`, change line 29:

```python
# Before:
from core.llm import complete, complete_tdd

# After:
from core.llm import complete
```

- [ ] **Step 2: Replace the complete_tdd() call**

Find the usage of `complete_tdd` (around line 257). Replace:

```python
response = await complete_tdd(prompt=prompt, cwd=repo_dir)
```

With:

```python
response = await complete(agent="dev", prompt=prompt, cwd=repo_dir)
```

- [ ] **Step 3: Verify no remaining references to complete_tdd**

```bash
grep -n "complete_tdd" /Users/nomi/Documents/88hours/engineering-wf/helix-community/agents/dev/agent.py
```

Expected: no output.

- [ ] **Step 4: Run the dev agent tests**

```bash
cd /Users/nomi/Documents/88hours/engineering-wf/helix-community
python -m pytest tests/agents/test_dev.py -v
```

Expected: All tests PASS (tests that mocked `complete_tdd` will need updating if any exist — see step 5).

- [ ] **Step 5: Update test_dev.py — replace all complete_tdd patches with complete**

`tests/agents/test_dev.py` patches `agents.dev.agent.complete_tdd` in 5 places (lines 219, 252, 280, 354, 381). After Task 7 Step 2, the dev agent no longer imports `complete_tdd`, so all 5 patches must change to `agents.dev.agent.complete`.

In each of these 5 locations, replace:

```python
patch("agents.dev.agent.complete_tdd", new=AsyncMock(return_value=TDD_PASSED)),
```
with:
```python
patch("agents.dev.agent.complete", new=AsyncMock(return_value=TDD_PASSED)),
```

And for the `side_effect` variant (line 252):
```python
patch("agents.dev.agent.complete_tdd", new=AsyncMock(side_effect=tdd_responses)),
```
becomes:
```python
patch("agents.dev.agent.complete", new=AsyncMock(side_effect=tdd_responses)),
```

And for the TDD_FAILED variants (lines 280, 354, 381 if applicable — verify with grep output):
```python
patch("agents.dev.agent.complete_tdd", new=AsyncMock(return_value=TDD_FAILED)),
```
becomes:
```python
patch("agents.dev.agent.complete", new=AsyncMock(return_value=TDD_FAILED)),
```

- [ ] **Step 6: Commit**

```bash
git add agents/dev/agent.py tests/agents/test_dev.py
git commit -m "Dev agent: replace complete_tdd() with complete(agent='dev', cwd=...) to use per-agent routing"
```

---

## Task 8: Create core/preflight.py

**Files:**
- Create: `core/preflight.py`

This is a new file matching helix-server exactly. It checks that at least one LLM provider key is set (or Bedrock is configured via IAM — which needs no stored key).

- [ ] **Step 1: Create the file**

Create `core/preflight.py` with this content:

```python
import logging
import os

logger = logging.getLogger(__name__)


def _agent_in_config(agent: str) -> bool:
    from core.config import get_agent_config
    try:
        get_agent_config(agent)
        return True
    except KeyError:
        return False


def check_required_env():
    from core.config import get_agent_config
    has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))
    has_openrouter = bool(os.environ.get("OPENROUTER_API_KEY"))
    has_bedrock = any(
        get_agent_config(a).provider == "bedrock"
        for a in ("crash_handler", "qa", "dev")
        if _agent_in_config(a)
    )
    if not (has_anthropic or has_openrouter or has_bedrock):
        raise RuntimeError(
            "No LLM provider key set. Set ANTHROPIC_API_KEY or OPENROUTER_API_KEY, or configure provider: bedrock in config.yaml."
        )
    for var in ("SENTRY_WEBHOOK_SECRET", "ROLLBAR_ACCESS_TOKEN", "GITHUB_TOKEN"):
        if not os.environ.get(var):
            logger.warning("Optional env var %s not set — related features will be disabled.", var)
```

- [ ] **Step 2: Write a test for preflight**

Create `tests/core/test_preflight.py`:

```python
"""Tests for core/preflight.py"""
import pytest
from unittest.mock import patch, MagicMock

from core.config import AgentConfig
from core.preflight import check_required_env


def _make_agent_config(provider: str) -> AgentConfig:
    return AgentConfig(agent="crash_handler", provider=provider, model="claude-haiku-4-5-20251001")


SAMPLE_YAML = {
    "agents": {
        "crash_handler": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
        "qa": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
        "dev": {"provider": "claude-code", "model": "claude-sonnet-4-6"},
    },
}


def test_check_required_env_anthropic_passes(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    with patch("core.config._load_yaml", return_value=SAMPLE_YAML):
        check_required_env()  # should not raise


def test_check_required_env_openrouter_passes(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    with patch("core.config._load_yaml", return_value=SAMPLE_YAML):
        check_required_env()  # should not raise


def test_check_required_env_bedrock_passes(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    bedrock_yaml = {
        "agents": {
            "crash_handler": {"provider": "bedrock", "model": "claude-haiku-4-5"},
            "qa": {"provider": "bedrock", "model": "claude-haiku-4-5"},
            "dev": {"provider": "claude-code", "model": "claude-sonnet-4-6"},
        }
    }
    with patch("core.config._load_yaml", return_value=bedrock_yaml):
        check_required_env()  # should not raise — bedrock needs no stored key


def test_check_required_env_no_provider_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with patch("core.config._load_yaml", return_value=SAMPLE_YAML):
        with pytest.raises(RuntimeError, match="No LLM provider key set"):
            check_required_env()
```

- [ ] **Step 3: Run the preflight tests**

```bash
cd /Users/nomi/Documents/88hours/engineering-wf/helix-community
python -m pytest tests/core/test_preflight.py -v
```

Expected: All 4 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add core/preflight.py tests/core/test_preflight.py
git commit -m "Feat: add core/preflight.py for startup env validation (supports Bedrock IAM, Anthropic, OpenRouter)"
```

---

## Task 9: Full test suite and final verification

- [ ] **Step 1: Run the full test suite**

```bash
cd /Users/nomi/Documents/88hours/engineering-wf/helix-community
python -m pytest tests/ -v 2>&1 | tail -30
```

Expected: All tests PASS. No import errors for `LLMConfig` or `get_llm_config` (those are gone).

- [ ] **Step 2: Verify Bedrock config round-trip**

```bash
python -c "
from unittest.mock import patch
YAML = {
    'agents': {
        'crash_handler': {'provider': 'bedrock', 'model': 'claude-haiku-4-5'},
        'qa': {'provider': 'bedrock', 'model': 'claude-haiku-4-5'},
        'dev': {'provider': 'claude-code', 'model': 'claude-sonnet-4-6'},
        'notifier': {},
    },
    'settings': {'aws_bedrock_region': 'us-west-2'},
}
with patch('core.config._load_yaml', return_value=YAML):
    from core.config import get_agent_config, get_bedrock_region
    cfg = get_agent_config('crash_handler')
    print('provider:', cfg.provider)
    print('model:', cfg.model)
    print('region:', get_bedrock_region())
"
```

Expected:
```
provider: bedrock
model: claude-haiku-4-5
region: us-west-2
```

- [ ] **Step 3: Verify model ID mapping**

```bash
python -c "
from core.llm import _bedrock_model_id
cases = [
    ('claude-haiku-4-5', 'us-east-1', 'us.anthropic.claude-haiku-4-5-20251001-v1:0'),
    ('claude-sonnet-4-6', 'eu-west-1', 'eu.anthropic.claude-sonnet-4-6-20250514-v1:0'),
    ('claude-opus-4-6', 'ap-southeast-1', 'ap.anthropic.claude-opus-4-6-20250514-v1:0'),
]
for model, region, expected in cases:
    result = _bedrock_model_id(model, region)
    status = 'OK' if result == expected else f'FAIL: got {result}'
    print(f'{model} @ {region}: {status}')
"
```

Expected:
```
claude-haiku-4-5 @ us-east-1: OK
claude-sonnet-4-6 @ eu-west-1: OK
claude-opus-4-6 @ ap-southeast-1: OK
```

- [ ] **Step 4: Final commit**

```bash
git add docs/
git commit -m "Docs: add Bedrock community port implementation plan"
```
