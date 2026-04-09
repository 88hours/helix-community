"""
Event publishing and subscribing for the Helix agent pipeline.

Uses Redis Pub/Sub as the event backend.

Publishing:

    await publish(client, "crash_analysed", report.incident_id, report.model_dump())

Subscribing:

    async for incident_id, payload in subscribe(client, "crash_analysed"):
        ...  # handle event

Redis Pub/Sub channel names:

    helix:events:crash_analysed
    helix:events:test_case_generated
    helix:events:fix_suggested
    helix:events:pr_created
    helix:events:fix_failed
    helix:events:duplicate_detected
"""

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

import redis.asyncio as redis

logger = logging.getLogger(__name__)

# Redis Pub/Sub channel prefix.
_REDIS_CHANNEL_PREFIX = "helix:events"


# ---------------------------------------------------------------------------
# Redis Pub/Sub
# ---------------------------------------------------------------------------

async def _publish_pubsub(
    client: redis.Redis,
    event_name: str,
    incident_id: str,
    payload: dict,
) -> None:
    """Publish an event to a Redis Pub/Sub channel."""
    channel = f"{_REDIS_CHANNEL_PREFIX}:{event_name}"
    message = json.dumps({"incident_id": incident_id, "payload": payload})
    await client.publish(channel, message)
    logger.info(
        "event published",
        extra={"event": event_name, "incident_id": incident_id, "channel": channel},
    )


async def _subscribe_pubsub(
    client: redis.Redis,
    event_name: str,
) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
    """Subscribe to a Redis Pub/Sub channel and yield (incident_id, payload) tuples."""
    channel = f"{_REDIS_CHANNEL_PREFIX}:{event_name}"
    pubsub = client.pubsub()
    await pubsub.subscribe(channel)
    logger.info("subscribed to channel", extra={"event": event_name, "channel": channel})

    async for message in pubsub.listen():
        if message["type"] != "message":
            continue

        try:
            data = json.loads(message["data"])
            incident_id = data["incident_id"]
            payload = data["payload"]
        except (json.JSONDecodeError, KeyError) as exc:
            logger.error(
                "malformed pubsub message — skipping",
                extra={"channel": channel, "error": str(exc)},
            )
            continue

        logger.info(
            "event received",
            extra={"event": event_name, "incident_id": incident_id},
        )
        yield incident_id, payload


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def publish(
    client: redis.Redis,
    event_name: str,
    incident_id: str,
    payload: dict,
) -> None:
    """
    Publish a Helix pipeline event via Redis Pub/Sub.

    Args:
        client:      Async Redis client.
        event_name:  Short event name, e.g. "crash_analysed".
        incident_id: The incident this event belongs to.
        payload:     Event data — typically model.model_dump() for the
                     output model of the publishing agent.
    """
    await _publish_pubsub(client, event_name, incident_id, payload)


async def subscribe(
    client: redis.Redis,
    event_name: str,
    agent_name: str = "",
) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
    """
    Subscribe to a Helix pipeline event channel via Redis Pub/Sub.

    Yields (incident_id, payload) tuples as events arrive. Runs indefinitely
    until the caller breaks or the connection drops.

    Args:
        client:     Async Redis client.
        event_name: Short event name to subscribe to, e.g. "crash_analysed".
        agent_name: Name of the subscribing agent (informational only).
    """
    async for item in _subscribe_pubsub(client, event_name):
        yield item
