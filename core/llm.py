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
