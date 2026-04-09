"""
LLM router for the Helix agent pipeline.

Routes completion requests to the configured backend based on config.yaml
(or env var overrides):

    anthropic — Anthropic SDK, direct API calls (ANTHROPIC_API_KEY required)
    ollama    — OpenAI-compatible local inference; no API key needed
                (default base URL: http://localhost:11434)

Provider/model selection (highest wins):
    HELIX_PROVIDER         "anthropic" | "ollama"
    HELIX_MODEL            model ID or tag
    HELIX_OLLAMA_BASE_URL  Ollama server URL

All agents call the same function:

    response = await complete(agent="crash_handler", prompt="...", system="...")
"""

import logging
import os

from core.config import LLMConfig, get_llm_config

logger = logging.getLogger(__name__)

_MAX_TOKENS = 4096


# ---------------------------------------------------------------------------
# Anthropic backend
# ---------------------------------------------------------------------------

async def _complete_anthropic(
    config: LLMConfig, prompt: str, system: str
) -> tuple[str, dict]:
    """Call the Anthropic API directly using the Anthropic SDK."""
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

    message = await client.messages.create(**kwargs)

    usage = {
        "input_tokens": message.usage.input_tokens,
        "output_tokens": message.usage.output_tokens,
    }
    return message.content[0].text, usage


# ---------------------------------------------------------------------------
# Ollama backend  (OpenAI-compatible /v1/chat/completions endpoint)
# ---------------------------------------------------------------------------

async def _complete_ollama(
    config: LLMConfig, prompt: str, system: str
) -> tuple[str, dict]:
    """
    Call a locally hosted Ollama instance via its OpenAI-compatible API.

    Ollama exposes POST /v1/chat/completions at the base_url.  No API key is
    required for local instances; the Authorization header is omitted entirely.

    Args:
        config: LLMConfig with model tag (e.g. "llama3.2") and ollama_base_url.
        prompt: The user message.
        system: Optional system prompt (prepended as a system message if set).

    Returns:
        Tuple of (response text, usage dict with input/output token counts).
    """
    import httpx

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    url = config.ollama_base_url.rstrip("/") + "/v1/chat/completions"
    payload = {
        "model": config.model,
        "messages": messages,
        "stream": False,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()

    text = data["choices"][0]["message"]["content"]
    usage_raw = data.get("usage", {})
    usage = {
        "input_tokens": usage_raw.get("prompt_tokens", 0),
        "output_tokens": usage_raw.get("completion_tokens", 0),
    }
    return text, usage


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def complete(
    agent: str,
    prompt: str,
    system: str = "",
) -> str:
    """
    Run a completion via the configured LLM backend.

    Reads provider, model, and (for Ollama) base_url from config.yaml or
    environment variable overrides.  Agents are fully decoupled from the
    backend — they only call this function.

    Args:
        agent:  Agent name (used for logging only).
        prompt: The main prompt / user message.
        system: Optional system prompt.

    Returns:
        The model's text response as a plain string.

    Raises:
        ValueError:       Unknown provider in config.
        EnvironmentError: Missing ANTHROPIC_API_KEY when using Anthropic.
        httpx.HTTPError:  Network or HTTP error when using Ollama.
    """
    config: LLMConfig = get_llm_config()
    logger.info(
        "llm call",
        extra={"agent": agent, "provider": config.provider, "model": config.model},
    )

    if config.provider == "ollama":
        response, _ = await _complete_ollama(config, prompt, system)
    else:
        response, _ = await _complete_anthropic(config, prompt, system)

    return response
