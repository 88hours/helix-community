"""
LLM router for the Helix agent pipeline.

Routes completion requests to the Anthropic backend based on the agent's
configuration in config.yaml (or env var overrides):

    anthropic — Anthropic SDK, direct API calls (ANTHROPIC_API_KEY)

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
# Public API
# ---------------------------------------------------------------------------

async def complete(
    agent: str,
    prompt: str,
    system: str = "",
) -> str:
    """
    Run a completion via the Anthropic API.

    Args:
        agent:  Agent name (used for logging only).
        prompt: The main prompt / user message.
        system: Optional system prompt.

    Returns:
        The model's text response as a plain string.
    """
    config: LLMConfig = get_llm_config()
    logger.info("llm call", extra={"agent": agent, "model": config.model})
    response, _ = await _complete_anthropic(config, prompt, system)
    return response
