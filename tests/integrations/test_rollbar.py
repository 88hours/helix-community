"""Tests for integrations/rollbar.py"""
import pytest

from integrations.rollbar import parse_event, verify_token


TOKEN = "test-rollbar-access-token"


# ---------------------------------------------------------------------------
# verify_token
# ---------------------------------------------------------------------------

def _make_raw(token: str) -> dict:
    """Build a minimal payload with the token at the real Rollbar path."""
    return {
        "data": {
            "item": {
                "id": 1,
                "last_occurrence": {
                    "metadata": {"access_token": token}
                },
            }
        }
    }


def test_verify_token_valid():
    assert verify_token(_make_raw(TOKEN), TOKEN) is True


def test_verify_token_wrong_token():
    assert verify_token(_make_raw("wrong-token"), TOKEN) is False


def test_verify_token_missing_from_payload_allows_through():
    # Token absent — allowed (URL is the secret).
    assert verify_token({}, TOKEN) is True


def test_verify_token_occurrence_event_path():
    # "occurrence" events put the token at data.occurrence.metadata.access_token.
    raw = {"data": {"occurrence": {"metadata": {"access_token": TOKEN}}}}
    assert verify_token(raw, TOKEN) is True


def test_verify_token_occurrence_event_wrong_token():
    raw = {"data": {"occurrence": {"metadata": {"access_token": "wrong"}}}}
    assert verify_token(raw, TOKEN) is False


def test_verify_token_empty_configured_token():
    # Token present in payload but configured token is empty — reject.
    assert verify_token(_make_raw(TOKEN), "") is False


# ---------------------------------------------------------------------------
# parse_event
# ---------------------------------------------------------------------------

RAW_PAYLOAD = {
    "event_name": "new_item",
    "data": {
        "item": {
            "id": 12345,
            "title": "KeyError: 'item_id'",
            "level": "error",
            "environment": "production",
            "project_id": 654321,
            "last_occurrence": {
                "id": "occ-uuid-001",
                "language": "python",
                "context": "checkout.process",
                "metadata": {"access_token": TOKEN},
                "body": {
                    "trace": {
                        "frames": [
                            {
                                "filename": "checkout.py",
                                "lineno": 42,
                                "method": "process",
                                "code": "    item = cart[item_id]",
                            }
                        ],
                        "exception": {
                            "class": "KeyError",
                            "message": "'item_id'",
                        },
                    }
                },
            },
        },
    },
}


def test_parse_event_basic_fields():
    event = parse_event(RAW_PAYLOAD)
    assert event.item_id == "12345"
    assert event.occurrence_id == "occ-uuid-001"
    assert event.title == "KeyError: 'item_id'"
    assert event.level == "error"
    assert event.environment == "production"
    assert event.language == "python"
    assert event.culprit == "checkout.process"
    assert event.project_id == 654321


def test_parse_event_stack_trace_extracted():
    event = parse_event(RAW_PAYLOAD)
    assert event.stack_trace is not None
    assert "checkout.py" in event.stack_trace
    assert "KeyError" in event.stack_trace
    assert "cart[item_id]" in event.stack_trace


def test_parse_event_no_frames_returns_none_stack_trace():
    payload = {
        "event_name": "new_item",
        "data": {
            "item": {
                "id": 999,
                "title": "Something broke",
                "level": "error",
                "last_occurrence": {
                    "id": "occ-002",
                    "body": {"trace": {"frames": [], "exception": {"class": "Error", "message": "oops"}}},
                },
            }
        },
    }
    event = parse_event(payload)
    assert event.stack_trace is None


def test_parse_event_no_occurrence_returns_none_stack_trace():
    payload = {
        "event_name": "new_item",
        "data": {
            "item": {
                "id": 888,
                "title": "Silent failure",
                "level": "warning",
                "last_occurrence": {"id": "occ-003", "body": {}},
            }
        },
    }
    event = parse_event(payload)
    assert event.stack_trace is None


def test_parse_event_raw_preserved():
    event = parse_event(RAW_PAYLOAD)
    assert event.raw == RAW_PAYLOAD


def test_parse_event_missing_data_uses_defaults():
    event = parse_event({})
    assert event.item_id == ""
    assert event.title == "Unknown error"
    assert event.stack_trace is None
