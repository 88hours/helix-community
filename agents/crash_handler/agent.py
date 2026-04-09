"""
Crash Handler Agent — core logic.

Receives a parsed RollbarEvent, calls the LLM to extract structured crash
information, persists the result to Redis, and publishes the crash_analysed
event to trigger the QA Agent.

Entry point: handle()
"""

import logging
import uuid

import redis.asyncio as redis

from agents.crash_handler import prompts
from core.events import publish
from core.llm import complete
from core.models import CrashReport, RollbarEvent, Severity
from core.state import write_crash_report, write_status
from core.utils import extract_json

logger = logging.getLogger(__name__)


async def handle(event: RollbarEvent, redis_client: redis.Redis) -> CrashReport:
    """
    Analyse a Rollbar/Sentry event and produce a structured CrashReport.

    Steps:
      1. Generate a unique incident_id.
      2. Call the LLM to extract severity, error details, and a plain-English summary.
      3. Persist the CrashReport to Redis.
      4. Publish the crash_analysed event to trigger the QA Agent.

    Args:
        event:        Normalised RollbarEvent from integrations/rollbar.py or sentry.py.
        redis_client: Async Redis client for state and event publishing.

    Returns:
        The persisted CrashReport.
    """
    incident_id = str(uuid.uuid4())
    logger.info(
        "crash handler started",
        extra={"incident_id": incident_id, "source": event.source, "source_item_id": event.item_id},
    )

    prompt = prompts.user(
        event_title=event.title,
        level=event.level or "error",
        culprit=event.culprit or "",
        stack_trace=event.stack_trace or "(no stack trace)",
        raw_summary=event.title,
        known_language=event.language or "",
        source=event.source,
    )

    raw_response = await complete(
        agent="crash_handler",
        prompt=prompt,
        system=prompts.SYSTEM,
    )

    data = extract_json(raw_response)

    language = (event.language or data.get("language") or "python").lower()

    report = CrashReport(
        incident_id=incident_id,
        source_item_id=event.item_id,
        source=event.source,
        severity=Severity(data["severity"]),
        error_type=data["error_type"],
        error_message=data["error_message"],
        stack_trace=data.get("stack_trace") or event.stack_trace or "",
        affected_component=data["affected_component"],
        affected_endpoint=data["affected_endpoint"],
        summary=data["summary"],
        language=language,
        raw_payload=event.raw,
    )

    await write_crash_report(redis_client, report)
    await write_status(redis_client, incident_id, "crash_analysed")
    await publish(redis_client, "crash_analysed", incident_id, report.model_dump(mode="json"))

    logger.info(
        "crash handler complete",
        extra={
            "incident_id": incident_id,
            "severity": report.severity,
            "affected_component": report.affected_component,
        },
    )
    return report
