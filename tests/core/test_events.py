"""Tests for core/events.py"""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from core.events import publish, subscribe


@pytest.fixture
def redis_mock():
    return AsyncMock()


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------

async def test_publish_sends_to_correct_channel(redis_mock):
    await publish(redis_mock, "crash_analysed", "inc-001", {"key": "value"})
    redis_mock.publish.assert_awaited_once()
    channel, message = redis_mock.publish.call_args[0]
    assert channel == "helix:events:crash_analysed"
    data = json.loads(message)
    assert data["incident_id"] == "inc-001"
    assert data["payload"] == {"key": "value"}


async def test_publish_encodes_payload_as_json(redis_mock):
    payload = {"severity": "high", "error": "KeyError"}
    await publish(redis_mock, "test_event", "inc-002", payload)
    _, message = redis_mock.publish.call_args[0]
    data = json.loads(message)
    assert data["payload"] == payload


# ---------------------------------------------------------------------------
# subscribe — yields valid events
# ---------------------------------------------------------------------------

async def test_subscribe_yields_events():
    raw_message = json.dumps({"incident_id": "inc-001", "payload": {"key": "val"}}).encode()

    async def fake_listen():
        yield {"type": "subscribe", "data": None}   # subscription ack — should be skipped
        yield {"type": "message", "data": raw_message}

    mock_pubsub = MagicMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.listen = fake_listen

    redis_client = AsyncMock()
    redis_client.pubsub = MagicMock(return_value=mock_pubsub)

    results = []
    async for incident_id, payload in subscribe(redis_client, "crash_analysed", agent_name="qa"):
        results.append((incident_id, payload))
        break

    assert results == [("inc-001", {"key": "val"})]


async def test_subscribe_skips_malformed_entries():
    async def fake_listen():
        yield {"type": "message", "data": b"not-json"}
        raise asyncio.CancelledError()

    mock_pubsub = MagicMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.listen = fake_listen

    redis_client = AsyncMock()
    redis_client.pubsub = MagicMock(return_value=mock_pubsub)

    results = []
    with pytest.raises(asyncio.CancelledError):
        async for incident_id, payload in subscribe(redis_client, "crash_analysed", agent_name="qa"):
            results.append((incident_id, payload))

    assert results == []


async def test_subscribe_subscribes_to_correct_channel():
    async def fake_listen():
        return
        yield  # make it an async generator

    mock_pubsub = MagicMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.listen = fake_listen

    redis_client = AsyncMock()
    redis_client.pubsub = MagicMock(return_value=mock_pubsub)

    async for _ in subscribe(redis_client, "test_case_generated"):
        pass

    mock_pubsub.subscribe.assert_awaited_once_with("helix:events:test_case_generated")
