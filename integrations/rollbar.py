"""
Rollbar integration for the Helix Crash Handler Agent.

Provides:
  verify_token  — access token verification against the token embedded in the payload
  parse_event   — normalise a raw Rollbar webhook payload into a RollbarEvent

Rollbar embeds the project post_server_item token at
data.item.last_occurrence.metadata.access_token in item-alert webhook payloads.
Helix verifies this matches the configured ROLLBAR_ACCESS_TOKEN to authenticate
the webhook.
"""

import logging
from typing import Any

from core.models import RollbarEvent

logger = logging.getLogger(__name__)


def verify_token(raw: dict[str, Any], access_token: str) -> bool:
    """
    Verify a Rollbar webhook by comparing the access_token in the payload
    against the configured project token.

    Rollbar embeds the token inconsistently depending on event type:
      - new_item / exp_repeat_item: data.item.last_occurrence.metadata.access_token
      - occurrence:                 data.occurrence.metadata.access_token

    If the token is absent from the payload entirely (some event types omit it),
    the request is allowed through — the webhook URL itself acts as the secret.
    If the token IS present but does not match, the request is rejected.

    Args:
        raw:          The parsed JSON body of the Rollbar webhook POST.
        access_token: ROLLBAR_ACCESS_TOKEN from the environment.

    Returns:
        True if the token matches or is absent; False if present but wrong.
    """
    data: dict = raw.get("data", {})

    # Try both known locations.
    payload_token = (
        _nested(data, "item", "last_occurrence", "metadata", "access_token")
        or _nested(data, "occurrence", "metadata", "access_token")
    )

    if not payload_token:
        # Token absent — allow through, rely on URL secrecy.
        return True

    return payload_token == access_token


def _nested(d: dict, *keys: str) -> str:
    """Safely traverse nested dicts; return empty string if any key is missing."""
    for key in keys:
        if not isinstance(d, dict):
            return ""
        d = d.get(key, {})
    return d if isinstance(d, str) else ""


def parse_event(raw: dict[str, Any]) -> RollbarEvent:
    """
    Normalise a raw Rollbar webhook payload into a RollbarEvent.

    Handles the standard Rollbar item-alert webhook shape (new_item,
    reactivated_item, occurrence). Preserves the full raw dict so downstream
    agents can access any field not explicitly mapped here.

    Args:
        raw: The parsed JSON body of the Rollbar webhook POST.

    Returns:
        A RollbarEvent with all available fields populated.
    """
    data: dict[str, Any] = raw.get("data", {})
    item: dict[str, Any] = data.get("item", {})
    # "occurrence" events use data.occurrence; other events use data.item.last_occurrence.
    occurrence: dict[str, Any] = data.get("occurrence") or item.get("last_occurrence", {})

    item_id = str(item.get("id", ""))
    occurrence_id = str(occurrence.get("id", "") or item_id)

    title = item.get("title") or "Unknown error"
    # item.level is an integer in Rollbar's API (40 = error, 50 = critical, etc.)
    # occurrence.level is already a string ("error", "warning", etc.) — prefer it.
    raw_level = occurrence.get("level") or item.get("level")
    level = str(raw_level) if raw_level is not None else None
    environment = item.get("environment") or occurrence.get("environment")
    language = occurrence.get("language")
    culprit = occurrence.get("context")

    stack_trace = _extract_stack_trace(occurrence)

    # Rollbar item URL: constructed from project_id and item counter if not provided.
    url = item.get("url")

    logger.info(
        "rollbar event parsed",
        extra={"item_id": item_id, "level": level},
    )

    return RollbarEvent(
        item_id=item_id,
        occurrence_id=occurrence_id,
        title=title,
        level=level,
        environment=environment,
        language=language,
        culprit=culprit,
        stack_trace=stack_trace,
        url=url,
        project_id=item.get("project_id"),
        raw=raw,
    )


def _extract_stack_trace(occurrence: dict[str, Any]) -> str | None:
    """
    Build a human-readable stack trace string from a Rollbar occurrence.

    Processes the trace frames from the occurrence body, most recent last,
    in the same format Python uses for tracebacks.

    Args:
        occurrence: The last_occurrence dict from the Rollbar item payload.

    Returns:
        Formatted stack trace string, or None if no frames are present.
    """
    body: dict[str, Any] = occurrence.get("body", {})
    trace: dict[str, Any] = body.get("trace", {})

    frames: list[dict] = trace.get("frames", [])
    if not frames:
        # Rollbar also supports trace_chain (chained exceptions)
        chain: list[dict] = body.get("trace_chain", [])
        if chain:
            frames = chain[0].get("frames", [])

    if not frames:
        return None

    lines = ["Traceback (most recent call last):"]
    for frame in frames:
        filename = frame.get("filename", "<unknown>")
        lineno = frame.get("lineno", "?")
        method = frame.get("method") or frame.get("function", "<unknown>")
        code = (frame.get("code") or frame.get("context_line", "")).strip()
        lines.append(f'  File "{filename}", line {lineno}, in {method}')
        if code:
            lines.append(f"    {code}")

    exc: dict[str, Any] = trace.get("exception", {})
    exc_class = exc.get("class", "Exception")
    exc_message = exc.get("message", "")
    lines.append(f"{exc_class}: {exc_message}")

    return "\n".join(lines)
