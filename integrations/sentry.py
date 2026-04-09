"""
Sentry integration for the Helix Crash Handler Agent.

Provides:
  verify_signature — HMAC-SHA256 signature verification for inbound Sentry webhooks
  parse_event      — normalise a raw Sentry webhook payload into a RollbarEvent

Sentry signs every webhook request with HMAC-SHA256 using the client secret
configured in the Sentry integration settings.  The signature is sent in the
`sentry-hook-signature` request header as a lowercase hex digest (no prefix).

Helix handles Sentry issue-alert webhooks, which carry the full event payload at
`data.event`.  The normalised output is a RollbarEvent(source="sentry") so the
Crash Handler Agent requires no changes.
"""

import hashlib
import hmac
import logging
from typing import Any

from core.models import RollbarEvent

logger = logging.getLogger(__name__)


def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    """
    Verify a Sentry webhook request signature.

    Sentry computes HMAC-SHA256 over the raw request body using the client
    secret and sends the lowercase hex digest in the `sentry-hook-signature`
    header.

    Args:
        body:      Raw request body bytes.
        signature: Value of the `sentry-hook-signature` header.
        secret:    Client secret from the Sentry integration settings
                   (SENTRY_WEBHOOK_SECRET env var).

    Returns:
        True if the signature is valid; False otherwise.
    """
    if not secret or not signature:
        return False

    expected = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature.lower())


def parse_event(raw: dict[str, Any]) -> RollbarEvent:
    """
    Normalise a raw Sentry webhook payload into a RollbarEvent.

    Handles Sentry issue-alert webhook payloads (action: "triggered"), which
    carry the full exception and stack trace at data.event.  Also handles
    issue-created / issue-resolved webhooks where only data.issue is present,
    though stack trace extraction is best-effort in that case.

    Args:
        raw: The parsed JSON body of the Sentry webhook POST.

    Returns:
        A RollbarEvent with source="sentry" and all available fields populated.
    """
    data: dict[str, Any] = raw.get("data", {})
    event: dict[str, Any] = data.get("event", {})
    issue: dict[str, Any] = data.get("issue", {})

    # Issue ID — prefer the explicit issue dict, fall back to event.issue_id.
    item_id = str(issue.get("id") or event.get("issue_id") or "")

    # Event / occurrence ID.
    occurrence_id = str(event.get("event_id") or item_id)

    # Title: event.title is most accurate; fall back to issue.title.
    title = event.get("title") or issue.get("title") or "Unknown error"

    # Severity level — Sentry uses "error", "warning", "fatal", "info".
    level = event.get("level") or issue.get("level")

    # Environment tag (e.g. "production", "staging").
    environment = _tag_value(event, "environment") or issue.get("environment")

    # Language: Sentry calls it "platform" and uses values like "python",
    # "javascript", "node", "ruby", "java", "go".
    platform = event.get("platform") or issue.get("platform") or ""
    language = _normalise_platform(platform)

    # Culprit: the function / file Sentry identifies as the origin.
    culprit = event.get("culprit") or issue.get("culprit")

    # Stack trace: extracted from the exception chain in the event payload.
    stack_trace = _extract_stack_trace(event)

    # Permalink to the issue in Sentry.
    url = (
        event.get("issue_url")
        or issue.get("permalink")
        or issue.get("url")
    )

    # Project ID (integer) — present in both event and issue dicts.
    raw_project_id = event.get("project") or issue.get("project", {})
    project_id: int | None = None
    if isinstance(raw_project_id, int):
        project_id = raw_project_id
    elif isinstance(raw_project_id, dict):
        try:
            project_id = int(raw_project_id.get("id", 0)) or None
        except (TypeError, ValueError):
            project_id = None

    logger.info(
        "sentry event parsed",
        extra={"item_id": item_id, "level": level, "platform": platform},
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
        project_id=project_id,
        source="sentry",
        raw=raw,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalise_platform(platform: str) -> str:
    """
    Map a Sentry platform string to a canonical language name.

    Sentry uses "node" for Node.js projects; we normalise that to "javascript"
    so downstream language-to-test-framework mapping works correctly.

    Args:
        platform: Sentry platform string, e.g. "python", "node", "ruby".

    Returns:
        Canonical language string, e.g. "python", "javascript", "ruby".
    """
    mapping = {
        "node": "javascript",
        "javascript": "javascript",
        "typescript": "typescript",
        "python": "python",
        "ruby": "ruby",
        "java": "java",
        "kotlin": "kotlin",
        "go": "go",
        "csharp": "csharp",
        "php": "php",
    }
    return mapping.get(platform.lower(), platform.lower())


def _tag_value(event: dict[str, Any], key: str) -> str | None:
    """
    Extract a tag value from event.tags.

    Sentry encodes tags as either a list of [key, value] pairs or a list of
    {"key": ..., "value": ...} dicts depending on the SDK version.

    Args:
        event: The event dict from the Sentry webhook payload.
        key:   Tag name to look up, e.g. "environment".

    Returns:
        The tag value string, or None if not found.
    """
    for tag in event.get("tags", []):
        if isinstance(tag, (list, tuple)) and len(tag) == 2 and tag[0] == key:
            return str(tag[1])
        if isinstance(tag, dict) and tag.get("key") == key:
            return str(tag.get("value", ""))
    return None


def _extract_stack_trace(event: dict[str, Any]) -> str | None:
    """
    Build a human-readable stack trace string from a Sentry event.

    Sentry stores exceptions in event.exception.values (a list to support
    chained exceptions).  We format the innermost exception's frames in the
    same style as Python tracebacks so the QA Agent's path-extraction logic
    works regardless of the crash source.

    Args:
        event: The event dict from the Sentry webhook payload.

    Returns:
        Formatted stack trace string, or None if no frames are present.
    """
    exception: dict[str, Any] = event.get("exception", {})
    values: list[dict] = exception.get("values", [])

    if not values:
        return None

    # Use the last (innermost) exception in the chain.
    exc = values[-1]
    exc_type = exc.get("type", "Exception")
    exc_value = exc.get("value", "")
    frames: list[dict] = exc.get("stacktrace", {}).get("frames", [])

    if not frames:
        return None

    lines = ["Traceback (most recent call last):"]
    for frame in frames:
        filename = frame.get("filename") or frame.get("abs_path") or "<unknown>"
        lineno = frame.get("lineno", "?")
        function = frame.get("function", "<unknown>")
        context = (frame.get("context_line") or "").strip()
        lines.append(f'  File "{filename}", line {lineno}, in {function}')
        if context:
            lines.append(f"    {context}")

    lines.append(f"{exc_type}: {exc_value}")
    return "\n".join(lines)
