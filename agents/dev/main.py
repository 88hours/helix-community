"""
Dev Agent — subscriber entry point.

Subscribes to the test_case_generated Redis Pub/Sub channel.

Run with:
    python -m agents.dev.main
"""

import asyncio
import logging
import os

import redis.asyncio as aioredis

from agents.dev.agent import handle
from core.config import get_redis_url
from core.events import subscribe
from core.models import QAResult
from core.state import read_crash_report, read_qa_result

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


async def main() -> None:
    """Subscribe to test_case_generated events and run the Dev Agent for each one."""
    logger.info("=== Dev Agent starting ===")
    redis_url = get_redis_url()
    redis_client = aioredis.from_url(redis_url, decode_responses=False)
    logger.info("dev agent subscriber started — listening on helix:events:test_case_generated")

    async for incident_id, payload in subscribe(redis_client, "test_case_generated", agent_name="dev"):
        logger.info("dev agent received event", extra={"incident_id": incident_id})
        try:
            qa_result = await read_qa_result(redis_client, incident_id)
            if qa_result is None:
                logger.warning(
                    "qa_result not in redis — falling back to event payload",
                    extra={"incident_id": incident_id},
                )
                qa_result = QAResult.model_validate(payload)

            crash_report = await read_crash_report(redis_client, incident_id)
            if crash_report is None:
                logger.error("crash_report missing — skipping", extra={"incident_id": incident_id})
                continue

            await handle(qa_result, crash_report, redis_client)
            logger.info("dev agent finished handling incident", extra={"incident_id": incident_id})
        except Exception as exc:
            logger.error(
                "dev agent failed",
                extra={"incident_id": incident_id, "error": str(exc)},
                exc_info=True,
            )


if __name__ == "__main__":
    asyncio.run(main())
