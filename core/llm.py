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

The Dev Agent's TDD loop uses a separate entry point:

    response = await complete_tdd(prompt="...", cwd="/path/to/repo")

This always invokes the Claude Code CLI as a subprocess inside the cloned
repo so it can run shell commands (pytest, jest, etc.) to verify fixes.
"""

import asyncio
import logging
import os

from core.config import LLMConfig, get_llm_config

logger = logging.getLogger(__name__)

_MAX_TOKENS = 4096

# Per-call timeout for the claude-code subprocess (10 minutes).
# The Dev Agent's outer TDD timeout (8 min) fires first on healthy runs;
# this exists as a hard backstop against a hung subprocess.
_SUBPROCESS_TIMEOUT = 600


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

# ---------------------------------------------------------------------------
# Claude Code backend  (subprocess — Dev Agent TDD loop only)
# ---------------------------------------------------------------------------

async def _complete_claude_code(prompt: str, cwd: str) -> str:
    """
    Invoke the Claude Code CLI as a subprocess inside the cloned repo.

    Args:
        prompt: Prompt to send to Claude Code.
        cwd:    Working directory for the subprocess (the cloned repo root).

    Returns:
        The CLI's stdout as a plain string.

    Raises:
        RuntimeError: If the subprocess exits non-zero or times out.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            "claude", "--dangerously-skip-permissions", "-p", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=_SUBPROCESS_TIMEOUT
        )
    except asyncio.TimeoutError:
        try:
            process.kill()
        except Exception:
            pass
        raise RuntimeError(
            f"claude-code subprocess timed out after {_SUBPROCESS_TIMEOUT}s"
        )

    if process.returncode != 0:
        raise RuntimeError(
            f"claude-code exited {process.returncode}: {stderr.decode().strip()}"
        )
    return stdout.decode()


async def complete_tdd(prompt: str, cwd: str) -> str:
    """
    Run a TDD fix cycle using the Claude Code CLI inside a cloned repo.

    This is the only function in Helix that invokes the claude-code CLI.
    It needs to be installed and authenticated on the host machine.

    Args:
        prompt: TDD prompt built by agents.dev.prompts.build_tdd().
        cwd:    Path to the cloned repo root.

    Returns:
        The CLI's stdout — contains a TESTS_PASSED or TESTS_FAILED sentinel.
    """
    logger.info("claude-code tdd call", extra={"cwd": cwd})
    return await _complete_claude_code(prompt, cwd)


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
