"""
Notifier Agent — core logic.

Handles all outbound Slack notifications for the pipeline.

Entry points:
  handle()             — fix_suggested events; sends the team a link to the GitHub Issue.
  handle_escalation()  — fix_failed events; sends an escalation when Dev Agent exhausts retries.
  handle_pr_created()  — pr_created events; posts Slack approval request with Approve/Reject buttons.
  handle_duplicate()   — duplicate_detected events; notifies the team of a recurring crash.
"""

import logging

import redis.asyncio as redis

from core.config import get_slack_config
from core.state import read_crash_report, read_pr_result
from integrations import slack

logger = logging.getLogger(__name__)


async def handle(
    incident_id: str,
    issue_url: str,
    redis_client: redis.Redis,
) -> None:
    """
    Send a Slack notification for a suggested fix.

    Args:
        incident_id:  Helix incident ID.
        issue_url:    URL of the GitHub Issue where the fix was posted.
        redis_client: Async Redis client.
    """
    logger.info("notifier agent started", extra={"incident_id": incident_id})

    slack_config = get_slack_config()
    crash_report = await read_crash_report(redis_client, incident_id)

    if crash_report is None:
        logger.warning("crash report not found — sending notification without error context", extra={"incident_id": incident_id})
        error_type = "unknown"
        error_message = "unknown"
        affected_component = "unknown"
    else:
        error_type = crash_report.error_type
        error_message = crash_report.error_message
        affected_component = crash_report.affected_component

    slack_text = (
        f":wrench: *Helix suggested a fix* for incident `{incident_id}`\n"
        f"*Error:* `{error_type}: {error_message}`\n"
        f"*Component:* {affected_component}\n"
        f"*Review the fix:* {issue_url}"
    )
    await slack.post_message(
        text=slack_text,
        channel=slack_config.approval_channel,
        token=slack_config.token,
    )
    logger.info("slack notification sent", extra={"incident_id": incident_id})


async def handle_escalation(
    incident_id: str,
    crash_summary: str,
    attempts: int,
    context: str,
    redis_client: redis.Redis,
) -> None:
    """
    Send escalation notifications when the Dev Agent exhausts all retries.

    Args:
        incident_id:   Helix incident ID.
        crash_summary: Plain-English crash summary from the Crash Handler.
        attempts:      Number of fix attempts that were made.
        context:       Per-attempt summaries from the Dev Agent.
        redis_client:  Async Redis client.
    """
    logger.info("notifier agent escalating", extra={"incident_id": incident_id})

    slack_config = get_slack_config()
    await slack.post_escalation(
        incident_id=incident_id,
        crash_summary=crash_summary,
        attempts=attempts,
        context=context,
        channel=slack_config.approval_channel,
        token=slack_config.token,
    )
    logger.info("slack escalation sent", extra={"incident_id": incident_id})


async def handle_pr_created(
    incident_id: str,
    redis_client: redis.Redis,
) -> None:
    """
    Post a Slack approval request when the Dev Agent has created a PR.

    No-op if SLACK_BOT_TOKEN or SLACK_APPROVAL_CHANNEL is not set.

    Args:
        incident_id:  Helix incident ID.
        redis_client: Async Redis client.
    """
    logger.info("notifier handling pr_created", extra={"incident_id": incident_id})

    slack_config = get_slack_config()
    if not slack_config.token or not slack_config.approval_channel:
        logger.warning(
            "slack approval skipped — SLACK_BOT_TOKEN or SLACK_APPROVAL_CHANNEL not configured",
            extra={"incident_id": incident_id},
        )
        return

    pr_result = await read_pr_result(redis_client, incident_id)
    if pr_result is None:
        logger.error("pr_result not found — cannot send approval request", extra={"incident_id": incident_id})
        return

    await slack.post_approval_request(
        incident_id=incident_id,
        pr_url=pr_result.pr_url,
        pr_number=pr_result.pr_number,
        fix_summary=pr_result.fix_summary,
        channel=slack_config.approval_channel,
        token=slack_config.token,
    )
    logger.info("approval request sent", extra={"incident_id": incident_id, "pr_number": pr_result.pr_number})


async def handle_duplicate(
    incident_id: str,
    issue_url: str,
    error_type: str,
    error_message: str,
    redis_client: redis.Redis,
) -> None:
    """
    Notify the team when a crash recurs for a bug that already has an open issue.

    No-op if Slack is not configured.

    Args:
        incident_id:   Helix incident ID for the new occurrence.
        issue_url:     URL of the existing GitHub Issue.
        error_type:    Error class, e.g. "KeyError".
        error_message: Short error message.
        redis_client:  Async Redis client.
    """
    logger.info("notifier handling duplicate_detected", extra={"incident_id": incident_id})

    slack_config = get_slack_config()
    if not slack_config.token or not slack_config.approval_channel:
        logger.warning("duplicate notification skipped — Slack not configured", extra={"incident_id": incident_id})
        return

    text = (
        f":warning: *Recurring crash* — incident `{incident_id}`\n"
        f"*Error:* `{error_type}: {error_message}`\n"
        f"*Existing issue:* {issue_url}\n"
        f"This bug has been seen before. If a fix PR is already open, please review and approve it."
    )
    await slack.post_message(
        text=text,
        channel=slack_config.approval_channel,
        token=slack_config.token,
    )
    logger.info("duplicate notification sent", extra={"incident_id": incident_id})
