"""Tests for integrations/sentry.py"""
import hashlib
import hmac

import pytest

from integrations.sentry import parse_event, verify_signature


SECRET = "sentry-client-secret"


def _make_signature(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# verify_signature
# ---------------------------------------------------------------------------

def test_verify_signature_valid():
    body = b'{"action": "triggered"}'
    sig = _make_signature(body, SECRET)
    assert verify_signature(body, sig, SECRET) is True


def test_verify_signature_wrong_secret():
    body = b'{"action": "triggered"}'
    sig = _make_signature(body, "wrong-secret")
    assert verify_signature(body, sig, SECRET) is False


def test_verify_signature_tampered_body():
    body = b'{"action": "triggered"}'
    sig = _make_signature(body, SECRET)
    assert verify_signature(b'{"action": "tampered"}', sig, SECRET) is False


def test_verify_signature_empty_secret_returns_false():
    body = b'{"action": "triggered"}'
    sig = _make_signature(body, SECRET)
    assert verify_signature(body, sig, "") is False


def test_verify_signature_empty_signature_returns_false():
    body = b'{"action": "triggered"}'
    assert verify_signature(body, "", SECRET) is False


def test_verify_signature_uppercase_hex_accepted():
    body = b'{"action": "triggered"}'
    sig = _make_signature(body, SECRET).upper()
    assert verify_signature(body, sig, SECRET) is True


# ---------------------------------------------------------------------------
# parse_event — full issue-alert payload
# ---------------------------------------------------------------------------

ISSUE_ALERT_PAYLOAD = {
    "action": "triggered",
    "data": {
        "event": {
            "event_id": "evt-abc-123",
            "issue_id": 42,
            "title": "KeyError: 'user_id'",
            "level": "error",
            "platform": "python",
            "culprit": "checkout/views.py in process_order",
            "tags": [["environment", "production"], ["server", "web-1"]],
            "issue_url": "https://sentry.io/organizations/acme/issues/42/",
            "project": 7,
            "exception": {
                "values": [
                    {
                        "type": "KeyError",
                        "value": "'user_id'",
                        "stacktrace": {
                            "frames": [
                                {
                                    "filename": "checkout/views.py",
                                    "lineno": 88,
                                    "function": "process_order",
                                    "context_line": "    uid = request.data['user_id']",
                                }
                            ]
                        },
                    }
                ]
            },
        },
        "issue": {
            "id": 42,
            "title": "KeyError: 'user_id'",
            "level": "error",
            "platform": "python",
            "permalink": "https://sentry.io/organizations/acme/issues/42/",
        },
    },
}


def test_parse_event_basic_fields():
    event = parse_event(ISSUE_ALERT_PAYLOAD)
    assert event.item_id == "42"
    assert event.occurrence_id == "evt-abc-123"
    assert event.title == "KeyError: 'user_id'"
    assert event.level == "error"
    assert event.language == "python"
    assert event.culprit == "checkout/views.py in process_order"
    assert event.project_id == 7
    assert event.source == "sentry"


def test_parse_event_environment_from_tags():
    event = parse_event(ISSUE_ALERT_PAYLOAD)
    assert event.environment == "production"


def test_parse_event_url_from_issue_url():
    event = parse_event(ISSUE_ALERT_PAYLOAD)
    assert event.url == "https://sentry.io/organizations/acme/issues/42/"


def test_parse_event_stack_trace_extracted():
    event = parse_event(ISSUE_ALERT_PAYLOAD)
    assert event.stack_trace is not None
    assert "checkout/views.py" in event.stack_trace
    assert "KeyError" in event.stack_trace
    assert "request.data['user_id']" in event.stack_trace
    assert "Traceback (most recent call last):" in event.stack_trace


def test_parse_event_raw_preserved():
    event = parse_event(ISSUE_ALERT_PAYLOAD)
    assert event.raw == ISSUE_ALERT_PAYLOAD


def test_parse_event_empty_payload_uses_defaults():
    event = parse_event({})
    assert event.item_id == ""
    assert event.title == "Unknown error"
    assert event.stack_trace is None
    assert event.source == "sentry"


def test_parse_event_no_frames_returns_none_stack_trace():
    payload = {
        "data": {
            "event": {
                "event_id": "evt-no-frames",
                "title": "Something went wrong",
                "exception": {
                    "values": [
                        {
                            "type": "RuntimeError",
                            "value": "oops",
                            "stacktrace": {"frames": []},
                        }
                    ]
                },
            }
        }
    }
    event = parse_event(payload)
    assert event.stack_trace is None


def test_parse_event_no_exception_returns_none_stack_trace():
    payload = {
        "data": {
            "event": {"event_id": "evt-no-exc", "title": "No exception"},
        }
    }
    event = parse_event(payload)
    assert event.stack_trace is None


# ---------------------------------------------------------------------------
# parse_event — platform normalisation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("platform,expected", [
    ("python", "python"),
    ("node", "javascript"),
    ("javascript", "javascript"),
    ("ruby", "ruby"),
    ("java", "java"),
    ("go", "go"),
    ("kotlin", "kotlin"),
    ("csharp", "csharp"),
    ("php", "php"),
    ("typescript", "typescript"),
    ("unknown-platform", "unknown-platform"),
])
def test_normalise_platform(platform, expected):
    payload = {"data": {"event": {"platform": platform}}}
    event = parse_event(payload)
    assert event.language == expected


# ---------------------------------------------------------------------------
# parse_event — environment tag formats
# ---------------------------------------------------------------------------

def test_parse_event_environment_tag_dict_format():
    payload = {
        "data": {
            "event": {
                "tags": [{"key": "environment", "value": "staging"}],
            }
        }
    }
    event = parse_event(payload)
    assert event.environment == "staging"


def test_parse_event_environment_falls_back_to_issue():
    payload = {
        "data": {
            "event": {},
            "issue": {"environment": "production"},
        }
    }
    event = parse_event(payload)
    assert event.environment == "production"


# ---------------------------------------------------------------------------
# parse_event — project_id extraction
# ---------------------------------------------------------------------------

def test_parse_event_project_id_as_int():
    payload = {"data": {"event": {"project": 99}}}
    event = parse_event(payload)
    assert event.project_id == 99


def test_parse_event_project_id_as_dict():
    payload = {"data": {"event": {"project": {"id": "55"}}}}
    event = parse_event(payload)
    assert event.project_id == 55


def test_parse_event_project_id_missing_is_none():
    payload = {"data": {"event": {}}}
    event = parse_event(payload)
    assert event.project_id is None


# ---------------------------------------------------------------------------
# parse_event — chained exceptions use innermost
# ---------------------------------------------------------------------------

def test_parse_event_chained_exceptions_uses_innermost():
    payload = {
        "data": {
            "event": {
                "exception": {
                    "values": [
                        {
                            "type": "OuterError",
                            "value": "outer",
                            "stacktrace": {
                                "frames": [
                                    {"filename": "outer.py", "lineno": 1, "function": "f"}
                                ]
                            },
                        },
                        {
                            "type": "InnerError",
                            "value": "inner cause",
                            "stacktrace": {
                                "frames": [
                                    {"filename": "inner.py", "lineno": 10, "function": "g"}
                                ]
                            },
                        },
                    ]
                }
            }
        }
    }
    event = parse_event(payload)
    assert "InnerError: inner cause" in event.stack_trace
    assert "inner.py" in event.stack_trace
