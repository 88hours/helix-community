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
