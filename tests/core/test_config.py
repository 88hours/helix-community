"""Tests for core/config.py"""
import pytest
from unittest.mock import patch

from core.config import (
    get_llm_config,
    get_redis_url,
    get_rollbar_config,
    get_sentry_config,
    get_github_config,
    get_slack_config,
)

SAMPLE_YAML = {
    "llm": {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "ollama_base_url": "http://localhost:11434",
    },
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
# get_llm_config
# ---------------------------------------------------------------------------

def test_get_llm_config_returns_correct_values():
    cfg = get_llm_config()
    assert cfg.provider == "anthropic"
    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.ollama_base_url == "http://localhost:11434"


def test_get_llm_config_env_var_overrides(monkeypatch):
    monkeypatch.setenv("HELIX_PROVIDER", "ollama")
    monkeypatch.setenv("HELIX_MODEL", "llama3.2")
    monkeypatch.setenv("HELIX_OLLAMA_BASE_URL", "http://localhost:9999")
    cfg = get_llm_config()
    assert cfg.provider == "ollama"
    assert cfg.model == "llama3.2"
    assert cfg.ollama_base_url == "http://localhost:9999"


def test_get_llm_config_invalid_provider_raises(monkeypatch):
    monkeypatch.setenv("HELIX_PROVIDER", "openai")
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        get_llm_config()


def test_get_llm_config_missing_model_raises():
    yaml_no_model = {**SAMPLE_YAML, "llm": {"provider": "anthropic"}}
    with patch("core.config._load_yaml", return_value=yaml_no_model):
        with pytest.raises(ValueError, match="No LLM model configured"):
            get_llm_config()


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
