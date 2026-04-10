"""Tests for agents/crash_handler/agent.py and agents/crash_handler/main.py"""
import hashlib
import hmac
import json
import time
import urllib.parse
import pytest
from unittest.mock import ANY, AsyncMock, patch

from fastapi.testclient import TestClient

from core.models import CrashReport, PRResult, RollbarEvent, Severity


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

ROLLBAR_TOKEN = "test-rollbar-access-token"

SAMPLE_YAML = {
    "rollbar": {"access_token_env": "ROLLBAR_ACCESS_TOKEN"},
    "redis": {"url_env": "REDIS_URL", "ttl_seconds": 604800},
    "agents": {
        "crash_handler": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
    },
    "github": {
        "token_env": "GITHUB_TOKEN",
        "target_repo": "acme/repo",
        "base_branch": "main",
    },
    "jira": {
        "url_env": "JIRA_URL",
        "email_env": "JIRA_EMAIL",
        "token_env": "JIRA_TOKEN",
        "project_key_env": "JIRA_PROJECT_KEY",
    },
    "slack": {
        "token_env": "SLACK_BOT_TOKEN",
        "approval_channel_env": "SLACK_APPROVAL_CHANNEL",
        "signing_secret_env": "SLACK_SIGNING_SECRET",
        "approval_port": 8001,
    },
    "email": {
        "from_env": "EMAIL_FROM",
        "to_env": "EMAIL_TO",
        "sendgrid_api_key_env": "SENDGRID_API_KEY",
        "smtp_host_env": "SMTP_HOST",
        "smtp_port_env": "SMTP_PORT",
        "smtp_user_env": "SMTP_USER",
        "smtp_password_env": "SMTP_PASSWORD",
    },
}


@pytest.fixture(autouse=True)
def env_vars(monkeypatch):
    monkeypatch.setenv("ROLLBAR_ACCESS_TOKEN", ROLLBAR_TOKEN)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("EMAIL_FROM", "helix@acme.com")
    monkeypatch.setenv("EMAIL_TO", "oncall@acme.com")
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)


@pytest.fixture
def rollbar_event():
    return RollbarEvent(
        item_id="12345",
        occurrence_id="occ-uuid-001",
        title="KeyError: 'item_id'",
        level="error",
        environment="production",
        language="python",
        culprit="checkout.process",
        stack_trace='File "checkout.py", line 42, in process\n    item = cart[item_id]',
        raw={"data": {"item": {"id": 12345}}},
    )


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.set = AsyncMock(return_value=True)
    r.publish = AsyncMock(return_value=1)
    r.xadd = AsyncMock(return_value=b"1234567890-0")
    return r


LLM_RESPONSE = json.dumps({
    "severity": "high",
    "error_type": "KeyError",
    "error_message": "'item_id'",
    "stack_trace": "...",
    "affected_component": "checkout",
    "affected_endpoint": "/api/v1/checkout",
    "summary": "A KeyError occurred in the checkout process.",
    "language": "python",
})


# ---------------------------------------------------------------------------
# agent.handle()
# ---------------------------------------------------------------------------

async def test_handle_returns_crash_report(rollbar_event, mock_redis):
    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("agents.crash_handler.agent.complete", new=AsyncMock(return_value=LLM_RESPONSE)):
        from agents.crash_handler.agent import handle
        report = await handle(rollbar_event, mock_redis)

    assert isinstance(report, CrashReport)
    assert report.error_type == "KeyError"
    assert report.severity == Severity.high
    assert report.affected_component == "checkout"
    assert report.language == "python"


async def test_handle_uses_rollbar_language_over_llm(mock_redis):
    """Rollbar-provided language takes precedence over the LLM-detected one."""
    event = RollbarEvent(
        item_id="12345",
        occurrence_id="occ-uuid-002",
        title="TypeError: Cannot read property",
        level="error",
        language="javascript",
        stack_trace="at process (/app/checkout.js:10:5)",
        raw={},
    )
    llm_response = json.dumps({
        "severity": "high",
        "error_type": "TypeError",
        "error_message": "Cannot read property",
        "stack_trace": "...",
        "affected_component": "checkout",
        "affected_endpoint": "/api/checkout",
        "summary": "A TypeError in checkout.",
        "language": "python",  # LLM incorrectly detects python
    })
    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("agents.crash_handler.agent.complete", new=AsyncMock(return_value=llm_response)):
        from agents.crash_handler.agent import handle
        report = await handle(event, mock_redis)

    # Rollbar said "javascript" — that wins
    assert report.language == "javascript"


async def test_handle_falls_back_to_llm_language_when_rollbar_omits_it(mock_redis):
    """When Rollbar sends no language, the LLM-detected value is used."""
    event = RollbarEvent(
        item_id="12345",
        occurrence_id="occ-uuid-003",
        title="RuntimeError: boom",
        level="error",
        language=None,
        stack_trace="/app/main.go:42 +0x1234",
        raw={},
    )
    llm_response = json.dumps({
        "severity": "medium",
        "error_type": "RuntimeError",
        "error_message": "boom",
        "stack_trace": "...",
        "affected_component": "main",
        "affected_endpoint": "main()",
        "summary": "A runtime error in main.",
        "language": "go",
    })
    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("agents.crash_handler.agent.complete", new=AsyncMock(return_value=llm_response)):
        from agents.crash_handler.agent import handle
        report = await handle(event, mock_redis)

    assert report.language == "go"


async def test_handle_persists_to_redis(rollbar_event, mock_redis):
    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("agents.crash_handler.agent.complete", new=AsyncMock(return_value=LLM_RESPONSE)):
        from agents.crash_handler.agent import handle
        await handle(rollbar_event, mock_redis)

    # write_crash_report and write_status each call redis.set
    assert mock_redis.set.call_count >= 2


async def test_handle_publishes_event(rollbar_event, mock_redis):
    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("agents.crash_handler.agent.complete", new=AsyncMock(return_value=LLM_RESPONSE)):
        from agents.crash_handler.agent import handle
        await handle(rollbar_event, mock_redis)

    mock_redis.publish.assert_called_once()
    channel = mock_redis.publish.call_args[0][0]
    assert "crash_analysed" in channel


async def test_handle_uses_event_stack_trace_as_fallback(rollbar_event, mock_redis):
    response_no_trace = json.dumps({
        "severity": "high",
        "error_type": "KeyError",
        "error_message": "'item_id'",
        "affected_component": "checkout",
        "affected_endpoint": "/api/v1/checkout",
        "summary": "A bug.",
    })
    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("agents.crash_handler.agent.complete", new=AsyncMock(return_value=response_no_trace)):
        from agents.crash_handler.agent import handle
        report = await handle(rollbar_event, mock_redis)

    assert report.stack_trace == rollbar_event.stack_trace


# ---------------------------------------------------------------------------
# FastAPI webhook endpoint
# ---------------------------------------------------------------------------

RAW_ROLLBAR_PAYLOAD = {
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
                "metadata": {"access_token": ROLLBAR_TOKEN},
                "body": {
                    "trace": {
                        "frames": [
                            {
                                "filename": "checkout.py",
                                "lineno": 42,
                                "method": "process",
                                "code": "item = cart[item_id]",
                            }
                        ],
                        "exception": {"class": "KeyError", "message": "'item_id'"},
                    }
                },
            },
        },
    },
}


def _make_client():
    with patch("core.config._load_yaml", return_value=SAMPLE_YAML):
        from agents.crash_handler.main import app
        return TestClient(app, raise_server_exceptions=False)


def test_healthz():
    client = _make_client()
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_webhook_rollbar_test_ping_returns_202():
    client = _make_client()
    payload = {"event_name": "test", "data": {"message": "This is a test payload from Rollbar."}}
    resp = client.post(
        "/webhook/rollbar",
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 202
    assert resp.json() == {"status": "ok"}


def test_webhook_wrong_token_returns_401():
    import copy
    client = _make_client()
    payload = copy.deepcopy(RAW_ROLLBAR_PAYLOAD)
    payload["data"]["item"]["last_occurrence"]["metadata"]["access_token"] = "wrong"
    body = json.dumps(payload).encode()
    resp = client.post("/webhook/rollbar", content=body, headers={"content-type": "application/json"})
    assert resp.status_code == 401


def test_webhook_invalid_json_returns_400():
    client = _make_client()
    resp = client.post(
        "/webhook/rollbar",
        content=b"not-json",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400


def test_sentry_webhook_invalid_signature_returns_401():
    """When SENTRY_WEBHOOK_SECRET is set and the signature is wrong, the endpoint
    must reject the request with 401."""
    payload = {"action": "ping"}
    body = json.dumps(payload).encode()

    sample_yaml = {**SAMPLE_YAML, "sentry": {"webhook_secret_env": "SENTRY_WEBHOOK_SECRET"}}

    with patch("core.config._load_yaml", return_value=sample_yaml), \
         patch.dict("os.environ", {"SENTRY_WEBHOOK_SECRET": "real-secret"}):
        from agents.crash_handler.main import app
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/webhook/sentry",
                content=body,
                headers={
                    "content-type": "application/json",
                    "sentry-hook-signature": "invalid-signature",
                },
            )

    assert resp.status_code == 401


def test_webhook_valid_request_returns_202():
    body = json.dumps(RAW_ROLLBAR_PAYLOAD).encode()

    mock_report = CrashReport(
        incident_id="inc-001",
        source_item_id="12345", source="rollbar",
        severity=Severity.high,
        error_type="KeyError",
        error_message="'item_id'",
        stack_trace="...",
        affected_component="checkout",
        affected_endpoint="/api/checkout",
        summary="A bug.",
    )

    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("agents.crash_handler.main.handle", new=AsyncMock(return_value=mock_report)):
        from agents.crash_handler.main import app
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/webhook/rollbar",
                content=body,
                headers={"content-type": "application/json"},
            )

    assert resp.status_code == 202
    assert resp.json()["incident_id"] == "inc-001"


# ---------------------------------------------------------------------------
# /slack/actions endpoint
# ---------------------------------------------------------------------------

SIGNING_SECRET = "test-slack-signing-secret"

SAMPLE_PR_RESULT = PRResult(
    incident_id="inc-001",
    pr_url="https://github.com/acme/repo/pull/42",
    pr_number=42,
    branch_name="helix/fix-inc-001",
    iterations_taken=1,
    files_changed=["checkout.py"],
    fix_summary="Fixed the KeyError in checkout.",
)


def _slack_action_body(action_id: str, incident_id: str) -> bytes:
    """Build a URL-encoded Slack interaction payload."""
    payload = {
        "type": "block_actions",
        "actions": [{"action_id": action_id, "value": incident_id}],
    }
    return urllib.parse.urlencode({"payload": json.dumps(payload)}).encode()


def _slack_headers(body: bytes, secret: str) -> dict:
    ts = str(int(time.time()))
    base = f"v0:{ts}:{body.decode()}"
    sig = "v0=" + hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    return {
        "content-type": "application/x-www-form-urlencoded",
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": sig,
    }


def _make_slack_yaml():
    return {
        **SAMPLE_YAML,
        "slack": {
            "token_env": "SLACK_BOT_TOKEN",
            "approval_channel_env": "SLACK_APPROVAL_CHANNEL",
            "signing_secret_env": "SLACK_SIGNING_SECRET",
        },
    }


def test_slack_actions_missing_signing_secret_returns_403(monkeypatch):
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    body = _slack_action_body("approve_pr", "inc-001")
    ts = str(int(time.time()))
    headers = {
        "content-type": "application/x-www-form-urlencoded",
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": "v0=invalid",
    }
    with patch("core.config._load_yaml", return_value=_make_slack_yaml()):
        from agents.crash_handler.main import app
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/slack/actions", content=body, headers=headers)
    assert resp.status_code == 403


def test_slack_actions_invalid_signature_returns_403(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SIGNING_SECRET)
    body = _slack_action_body("approve_pr", "inc-001")
    ts = str(int(time.time()))
    headers = {
        "content-type": "application/x-www-form-urlencoded",
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": "v0=badsignature",
    }
    with patch("core.config._load_yaml", return_value=_make_slack_yaml()):
        from agents.crash_handler.main import app
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/slack/actions", content=body, headers=headers)
    assert resp.status_code == 403


def test_slack_actions_approve_merges_pr(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SIGNING_SECRET)
    monkeypatch.setenv("GITHUB_TOKEN", "gh-test-token")
    body = _slack_action_body("approve_pr", "inc-001")
    headers = _slack_headers(body, SIGNING_SECRET)

    with patch("core.config._load_yaml", return_value=_make_slack_yaml()), \
         patch("agents.crash_handler.main.read_pr_result", return_value=SAMPLE_PR_RESULT), \
         patch("agents.crash_handler.main.write_status", new=AsyncMock()) as mock_write_status, \
         patch("agents.crash_handler.main.merge_pull_request", new=AsyncMock()) as mock_merge:
        from agents.crash_handler.main import app
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/slack/actions", content=body, headers=headers)

    assert resp.status_code == 200
    assert "merged" in resp.json()["text"].lower()
    mock_merge.assert_awaited_once_with(repo="acme/repo", pr_number=42)
    mock_write_status.assert_awaited_once_with(ANY, "inc-001", "pr_merged")


def test_slack_actions_reject_updates_status(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SIGNING_SECRET)
    body = _slack_action_body("reject_pr", "inc-001")
    headers = _slack_headers(body, SIGNING_SECRET)

    with patch("core.config._load_yaml", return_value=_make_slack_yaml()), \
         patch("agents.crash_handler.main.write_status", new=AsyncMock()) as mock_write_status:
        from agents.crash_handler.main import app
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/slack/actions", content=body, headers=headers)

    assert resp.status_code == 200
    assert "rejected" in resp.json()["text"].lower()
    mock_write_status.assert_awaited_once_with(ANY, "inc-001", "approval_rejected")
