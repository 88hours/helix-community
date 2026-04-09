"""
Slack integration for the Helix agent pipeline.

Provides async helpers for posting messages and interactive approval requests
to Slack using the Web API (chat.postMessage).

Also provides verify_signature() for validating inbound Slack interaction payloads
sent when a user clicks an Approve / Reject button.

Required environment variables:
    SLACK_BOT_TOKEN        — Bot token with chat:write scope (xoxb-...)
    SLACK_SIGNING_SECRET   — Signing secret for verifying interaction payloads
    SLACK_APPROVAL_CHANNEL — Channel ID or name for human approval messages

All outbound functions are no-ops (with a logged warning) when the required
environment variables are absent, so the pipeline degrades gracefully when
Slack is not configured.
"""

import hashlib
import hmac
import logging
import os
import time
from typing import Optional

import httpx


logger = logging.getLogger(__name__)

_SLACK_API = "https://slack.com/api"

# Slack rejects requests older than this many seconds (replay attack prevention).
_MAX_TIMESTAMP_AGE = 300


# ---------------------------------------------------------------------------
# Signature verification (for interaction payloads)
# ---------------------------------------------------------------------------

def verify_signature(
    body: bytes,
    timestamp: str,
    signature: str,
    signing_secret: str,
) -> bool:
    """
    Verify a Slack request signature.

    Slack signs every request it sends (webhooks, interactions) using
    HMAC-SHA256 with the app's Signing Secret. This must be verified before
    processing any inbound Slack payload to prevent spoofing.

    Args:
        body:           Raw request body bytes.
        timestamp:      Value of the X-Slack-Request-Timestamp header.
        signature:      Value of the X-Slack-Signature header (format: "v0=<hex>").
        signing_secret: Slack app Signing Secret (from the app settings page).

    Returns:
        True if the signature is valid and the request is recent; False otherwise.
    """
    # Reject stale requests to prevent replay attacks.
    try:
        request_age = abs(time.time() - int(timestamp))
    except (ValueError, TypeError):
        return False
    if request_age > _MAX_TIMESTAMP_AGE:
        return False

    base = f"v0:{timestamp}:{body.decode('utf-8')}"
    expected = "v0=" + hmac.new(
        signing_secret.encode("utf-8"),
        base.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _auth_header(token: Optional[str] = None) -> dict[str, str]:
    """
    Return the Authorization header for Slack API calls.

    Args:
        token: Slack bot token. Falls back to SLACK_BOT_TOKEN env var.

    Raises:
        EnvironmentError: If no token can be resolved.
    """
    resolved = token or os.environ.get("SLACK_BOT_TOKEN")
    if not resolved:
        raise EnvironmentError("SLACK_BOT_TOKEN is not set")
    return {"Authorization": f"Bearer {resolved}"}


async def _post(payload: dict, token: Optional[str] = None) -> None:
    """
    Call chat.postMessage with the given Block Kit payload.

    Args:
        payload: Full Slack API payload dict (must include "channel").
        token:   Slack bot token. Falls back to SLACK_BOT_TOKEN env var.

    Raises:
        RuntimeError: If Slack returns ok=false.
        httpx.HTTPStatusError: On HTTP-level failures.
    """
    headers = {**_auth_header(token), "Content-Type": "application/json"}

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{_SLACK_API}/chat.postMessage",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()

    data = response.json()
    if not data.get("ok"):
        error = data.get("error", "unknown_error")
        raise RuntimeError(f"Slack API error: {error}")

    logger.info(
        "slack message posted",
        extra={"channel": payload.get("channel"), "error": None},
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def post_message(
    text: str,
    channel: Optional[str] = None,
    token: Optional[str] = None,
) -> None:
    """
    Post a plain-text message to a Slack channel.

    Logs a warning and returns without raising if SLACK_BOT_TOKEN or
    SLACK_APPROVAL_CHANNEL is not configured.

    Args:
        text:    Message body. Supports Slack mrkdwn formatting.
        channel: Channel ID or name. Defaults to SLACK_APPROVAL_CHANNEL env var.
        token:   Slack bot token. Defaults to SLACK_BOT_TOKEN env var.
    """
    resolved_token = token or os.environ.get("SLACK_BOT_TOKEN")
    if not resolved_token:
        logger.warning("slack notification skipped — SLACK_BOT_TOKEN not configured")
        return

    resolved_channel = channel or os.environ.get("SLACK_APPROVAL_CHANNEL")
    if not resolved_channel:
        logger.warning("slack notification skipped — SLACK_APPROVAL_CHANNEL not configured")
        return

    await _post({"channel": resolved_channel, "text": text}, resolved_token)


async def post_escalation(
    incident_id: str,
    crash_summary: str,
    attempts: int,
    context: str,
    channel: Optional[str] = None,
    token: Optional[str] = None,
) -> None:
    """
    Post a human-escalation message when the Dev Agent exhausts all retries.

    Includes the full context (crash report, what was tried) so the on-call
    engineer has everything they need without digging through logs.

    Args:
        incident_id:   Helix incident ID.
        crash_summary: Plain-English summary from the CrashReport.
        attempts:      Number of fix attempts made by the Dev Agent.
        context:       Full Dev Agent reasoning and what was tried.
        channel:       Channel ID or name. Defaults to SLACK_APPROVAL_CHANNEL.
        token:         Slack bot token. Defaults to SLACK_BOT_TOKEN.
    """
    resolved_token = token or os.environ.get("SLACK_BOT_TOKEN")
    if not resolved_token:
        logger.warning("slack escalation skipped — SLACK_BOT_TOKEN not configured")
        return

    resolved_channel = channel or os.environ.get("SLACK_APPROVAL_CHANNEL")
    if not resolved_channel:
        logger.warning("slack escalation skipped — SLACK_APPROVAL_CHANNEL not configured")
        return

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": ":sos: Helix — Dev Agent needs human help"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Incident:*\n`{incident_id}`"},
                {"type": "mrkdwn", "text": f"*Attempts exhausted:*\n{attempts}"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Crash summary:*\n{crash_summary}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*What the agent tried:*\n```{context[:2800]}```"},
        },
    ]

    await _post({"channel": resolved_channel, "blocks": blocks}, resolved_token)
    logger.info("escalation posted", extra={"incident_id": incident_id, "attempts": attempts})


async def post_approval_request(
    incident_id: str,
    pr_url: str,
    pr_number: int,
    fix_summary: str,
    channel: Optional[str] = None,
    token: Optional[str] = None,
) -> None:
    """
    Post a PR approval request to Slack with Approve / Reject buttons.

    The button action payloads carry the incident_id back to the
    POST /slack/actions endpoint on the crash handler so it can look up
    the PRResult and merge or reject accordingly.

    Logs a warning and returns without raising if SLACK_BOT_TOKEN or
    SLACK_APPROVAL_CHANNEL is not configured.

    Args:
        incident_id: Helix incident ID — embedded in button values.
        pr_url:      URL of the GitHub PR to review.
        pr_number:   GitHub PR number.
        fix_summary: Plain-English description of the fix from the Dev Agent.
        channel:     Channel ID or name. Defaults to SLACK_APPROVAL_CHANNEL.
        token:       Slack bot token. Defaults to SLACK_BOT_TOKEN.
    """
    resolved_token = token or os.environ.get("SLACK_BOT_TOKEN")
    if not resolved_token:
        logger.warning("approval request skipped — SLACK_BOT_TOKEN not configured")
        return

    resolved_channel = channel or os.environ.get("SLACK_APPROVAL_CHANNEL")
    if not resolved_channel:
        logger.warning("approval request skipped — SLACK_APPROVAL_CHANNEL not configured")
        return

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": ":white_check_mark: Helix — PR ready for review"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Incident:*\n`{incident_id}`"},
                {"type": "mrkdwn", "text": f"*Pull request:*\n<{pr_url}|PR #{pr_number}>"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Fix summary:*\n{fix_summary}"},
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve & Merge"},
                    "style": "primary",
                    "action_id": "approve_pr",
                    "value": incident_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "style": "danger",
                    "action_id": "reject_pr",
                    "value": incident_id,
                },
            ],
        },
    ]

    await _post({"channel": resolved_channel, "blocks": blocks}, resolved_token)
    logger.info(
        "approval request posted",
        extra={"incident_id": incident_id, "pr_number": pr_number},
    )
