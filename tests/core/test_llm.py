"""Tests for core/llm.py"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.config import LLMConfig
from core.llm import complete


def _make_config(provider: str, model: str = "claude-test") -> LLMConfig:
    return LLMConfig(provider=provider, model=model, ollama_base_url="http://localhost:11434")


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

    with patch("core.llm.get_llm_config", return_value=_make_config("anthropic")):
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await complete("crash_handler", "analyze this", system="you are an expert")

    assert result == "analysis result"


async def test_complete_anthropic_missing_key_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with patch("core.llm.get_llm_config", return_value=_make_config("anthropic")):
        with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
            await complete("crash_handler", "prompt")


async def test_complete_anthropic_no_system(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="result")]
    mock_msg.usage = MagicMock(input_tokens=50, output_tokens=20)
    mock_client = AsyncMock()
    mock_client.messages.create.return_value = mock_msg

    with patch("core.llm.get_llm_config", return_value=_make_config("anthropic")):
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await complete("crash_handler", "prompt")  # no system

    assert result == "result"
    call_kwargs = mock_client.messages.create.call_args[1]
    assert "system" not in call_kwargs


# ---------------------------------------------------------------------------
# Ollama backend
# ---------------------------------------------------------------------------

async def test_complete_ollama():
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "ollama result"}}],
        "usage": {"prompt_tokens": 80, "completion_tokens": 30},
    }
    mock_response.raise_for_status = MagicMock()

    mock_http_client = AsyncMock()
    mock_http_client.post.return_value = mock_response
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=None)

    with patch("core.llm.get_llm_config", return_value=_make_config("ollama", "llama3.2")):
        with patch("httpx.AsyncClient", return_value=mock_http_client):
            result = await complete("qa", "prompt", system="sys")

    assert result == "ollama result"


async def test_complete_ollama_no_system():
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "ollama result"}}],
        "usage": {},
    }
    mock_response.raise_for_status = MagicMock()

    mock_http_client = AsyncMock()
    mock_http_client.post.return_value = mock_response
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=None)

    with patch("core.llm.get_llm_config", return_value=_make_config("ollama", "llama3.2")):
        with patch("httpx.AsyncClient", return_value=mock_http_client):
            result = await complete("qa", "prompt")

    assert result == "ollama result"
    payload = mock_http_client.post.call_args[1]["json"]
    assert not any(m["role"] == "system" for m in payload["messages"])
