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
