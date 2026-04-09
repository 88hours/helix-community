"""
Configuration loader for Helix.

Reads settings from config.yaml and applies environment variable overrides.
No secrets are stored in config.yaml — only the names of the env vars that
hold them.

LLM model override:
    HELIX_MODEL              e.g. HELIX_MODEL=claude-haiku-4-5-20251001

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
    from core.config import get_llm_config, get_redis_url
    from core.config import get_rollbar_config, get_github_config, get_slack_config

    llm = get_llm_config()
    url = get_redis_url()
    gh = get_github_config()
"""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class LLMConfig:
    """Shared LLM settings used by all agents."""
    model: str      # model identifier, e.g. "claude-sonnet-4-6"


@dataclass
class RollbarConfig:
    """Rollbar webhook integration settings."""
    access_token: str   # Rollbar project read token


@dataclass
class SentryConfig:
    """Sentry webhook integration settings."""
    webhook_secret: str | None  # None → signature check skipped


@dataclass
class GitHubConfig:
    """GitHub integration settings."""
    target_repo: str    # "owner/name" of the repo Helix is fixing
    base_branch: str    # branch PRs are opened against
    token: str          # GitHub personal access token


@dataclass
class SlackConfig:
    """Slack integration settings."""
    token: str | None           # Slack bot token (xoxb-...)
    signing_secret: str | None  # used to verify Slack interaction payloads
    approval_channel: str | None  # channel for approval / escalation messages


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_yaml() -> dict:
    """Read and parse config.yaml."""
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(f"config.yaml not found at {_CONFIG_PATH}")
    with _CONFIG_PATH.open() as f:
        return yaml.safe_load(f)


def _require_env(var: str) -> str:
    """Return the value of an environment variable, raising if it is not set."""
    value = os.environ.get(var)
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{var}' is not set. "
            "Check your .env file or deployment config."
        )
    return value


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_llm_config() -> LLMConfig:
    """
    Return the shared LLM configuration used by all agents.

    Resolution order (highest wins):
      1. Environment variable HELIX_MODEL
      2. config.yaml llm.model
    """
    raw = _load_yaml()
    model = os.environ.get("HELIX_MODEL") or raw.get("llm", {}).get("model", "")
    if not model:
        raise ValueError("No LLM model configured. Set llm.model in config.yaml or HELIX_MODEL env var.")
    return LLMConfig(model=model)


def get_redis_url() -> str:
    """Return the Redis connection URL from the environment."""
    raw = _load_yaml()
    url_env = raw.get("redis", {}).get("url_env", "REDIS_URL")
    return _require_env(url_env)


def get_rollbar_config() -> RollbarConfig:
    """Return Rollbar webhook integration settings."""
    raw = _load_yaml()
    token_env = raw.get("rollbar", {}).get("access_token_env", "ROLLBAR_ACCESS_TOKEN")
    return RollbarConfig(access_token=_require_env(token_env))


def get_sentry_config() -> SentryConfig:
    """
    Return Sentry webhook integration settings.

    The webhook secret is optional — if SENTRY_WEBHOOK_SECRET is not set,
    signature verification is skipped with a warning.
    """
    raw = _load_yaml()
    secret_env = raw.get("sentry", {}).get("webhook_secret_env", "SENTRY_WEBHOOK_SECRET")
    return SentryConfig(webhook_secret=os.environ.get(secret_env) or None)


def get_github_config() -> GitHubConfig:
    """
    Return GitHub integration settings.

    Resolution order:
      1. Environment variable (HELIX_GITHUB_REPO, HELIX_GITHUB_BASE_BRANCH, GITHUB_TOKEN)
      2. config.yaml (github.target_repo, github.base_branch, github.token_env)
    """
    raw = _load_yaml()
    gh = raw.get("github", {})

    target_repo = os.environ.get("HELIX_GITHUB_REPO") or gh.get("target_repo", "")
    if not target_repo:
        raise ValueError(
            "github.target_repo is not set in config.yaml and HELIX_GITHUB_REPO is not set. "
            "Set it to 'owner/repo', e.g. 'acme/backend'."
        )

    base_branch = os.environ.get("HELIX_GITHUB_BASE_BRANCH") or gh.get("base_branch", "main")
    token_env = gh.get("token_env", "GITHUB_TOKEN")
    token = _require_env(token_env)

    return GitHubConfig(target_repo=target_repo, base_branch=base_branch, token=token)


def get_slack_config() -> SlackConfig:
    """
    Return Slack integration settings.

    Missing variables resolve to None — callers handle None gracefully
    (notifications are skipped with a warning rather than raising).
    """
    raw = _load_yaml()
    slack = raw.get("slack", {})

    token_env = slack.get("token_env", "SLACK_BOT_TOKEN")
    signing_secret_env = slack.get("signing_secret_env", "SLACK_SIGNING_SECRET")
    channel_env = slack.get("approval_channel_env", "SLACK_APPROVAL_CHANNEL")

    return SlackConfig(
        token=os.environ.get(token_env) or None,
        signing_secret=os.environ.get(signing_secret_env) or None,
        approval_channel=os.environ.get(channel_env) or None,
    )
