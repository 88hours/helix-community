"""
QA Agent — subscriber entry point.

Subscribes to the crash_analysed Redis Pub/Sub channel and calls the QA Agent
for every new incident.

Run with:
    python -m agents.qa.main
"""

import asyncio
import logging
import os

import redis.asyncio as aioredis

from agents.qa.agent import handle
from core.config import get_redis_url
from core.events import subscribe
from core.models import CrashReport
from core.state import read_crash_report

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


async def main() -> None:
    """
    Subscribe to crash_analysed events and run the QA Agent for each one.

    If the CrashReport is not in Redis (e.g. expired), the event is skipped
    with a warning rather than crashing the loop.
    """
    logger.info("=== QA Agent starting ===")
    redis_url = get_redis_url()
    redis_client = aioredis.from_url(redis_url, decode_responses=False)
    logger.info("qa agent subscriber started — listening on helix:events:crash_analysed")

    async for incident_id, payload in subscribe(redis_client, "crash_analysed", agent_name="qa"):
        logger.info("qa agent received event", extra={"incident_id": incident_id})
        try:
            report = await read_crash_report(redis_client, incident_id)
            if report is None:
                logger.warning(
                    "crash report not found in redis — falling back to event payload",
                    extra={"incident_id": incident_id},
                )
                report = CrashReport.model_validate(payload)

            await handle(report, redis_client)
            logger.info("qa agent finished handling incident", extra={"incident_id": incident_id})
        except Exception as exc:
            logger.error(
                "qa agent failed for incident",
                extra={"incident_id": incident_id, "error": str(exc)},
                exc_info=True,
            )


if __name__ == "__main__":
    asyncio.run(main())
