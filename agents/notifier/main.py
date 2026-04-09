"""
Notifier Agent — subscriber entry point.

Subscribes to four Redis Pub/Sub channels concurrently:
  fix_suggested      — sends Slack message with a link to the GitHub Issue.
  fix_failed         — sends Slack escalation when Dev Agent exhausts retries.
  pr_created         — sends Slack approval request with Approve/Reject buttons.
  duplicate_detected — notifies the team that a known bug has recurred.

Run with:
    python -m agents.notifier.main
"""

import asyncio
import logging
import os

import redis.asyncio as aioredis

from agents.notifier.agent import handle, handle_duplicate, handle_escalation, handle_pr_created
from core.config import get_redis_url
from core.events import subscribe

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


async def _listen_fix_suggested(redis_client: aioredis.Redis) -> None:
    """Subscribe to fix_suggested and dispatch handle() for each event."""
    logger.info("notifier listening on helix:events:fix_suggested")
    async for incident_id, payload in subscribe(redis_client, "fix_suggested", agent_name="notifier"):
        try:
            issue_url = payload.get("issue_url", "")
            if not issue_url:
                logger.warning("fix_suggested payload missing issue_url — skipping", extra={"incident_id": incident_id})
                continue
            await handle(incident_id, issue_url, redis_client)
        except Exception as exc:
            logger.error("notifier failed on fix_suggested", extra={"incident_id": incident_id, "error": str(exc)}, exc_info=True)


async def _listen_fix_failed(redis_client: aioredis.Redis) -> None:
    """Subscribe to fix_failed and dispatch handle_escalation() for each event."""
    logger.info("notifier listening on helix:events:fix_failed")
    async for incident_id, payload in subscribe(redis_client, "fix_failed", agent_name="notifier"):
        try:
            await handle_escalation(
                incident_id=incident_id,
                crash_summary=payload.get("crash_summary", ""),
                attempts=payload.get("attempts", 0),
                context=payload.get("context", "No attempts recorded."),
                redis_client=redis_client,
            )
        except Exception as exc:
            logger.error("notifier failed on fix_failed", extra={"incident_id": incident_id, "error": str(exc)}, exc_info=True)


async def _listen_pr_created(redis_client: aioredis.Redis) -> None:
    """Subscribe to pr_created and dispatch handle_pr_created() for each event."""
    logger.info("notifier listening on helix:events:pr_created")
    async for incident_id, _payload in subscribe(redis_client, "pr_created", agent_name="notifier"):
        try:
            await handle_pr_created(incident_id, redis_client)
        except Exception as exc:
            logger.error("notifier failed on pr_created", extra={"incident_id": incident_id, "error": str(exc)}, exc_info=True)


async def _listen_duplicate_detected(redis_client: aioredis.Redis) -> None:
    """Subscribe to duplicate_detected and dispatch handle_duplicate() for each event."""
    logger.info("notifier listening on helix:events:duplicate_detected")
    async for incident_id, payload in subscribe(redis_client, "duplicate_detected", agent_name="notifier"):
        try:
            await handle_duplicate(
                incident_id=incident_id,
                issue_url=payload.get("issue_url", ""),
                error_type=payload.get("error_type", "unknown"),
                error_message=payload.get("error_message", ""),
                redis_client=redis_client,
            )
        except Exception as exc:
            logger.error("notifier failed on duplicate_detected", extra={"incident_id": incident_id, "error": str(exc)}, exc_info=True)


async def main() -> None:
    """Connect to Redis and run all four subscription loops concurrently."""
    logger.info("=== Notifier Agent starting ===")
    redis_url = get_redis_url()
    redis_client = aioredis.from_url(redis_url, decode_responses=False)

    await asyncio.gather(
        _listen_fix_suggested(redis_client),
        _listen_fix_failed(redis_client),
        _listen_pr_created(redis_client),
        _listen_duplicate_detected(redis_client),
    )


if __name__ == "__main__":
    asyncio.run(main())
