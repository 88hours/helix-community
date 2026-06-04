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
