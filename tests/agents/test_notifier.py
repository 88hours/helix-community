"""Tests for agents/notifier/agent.py — handle_pr_created."""
import logging
import pytest
from unittest.mock import AsyncMock, patch

from core.models import PRResult


SAMPLE_YAML = {
    "redis": {"url_env": "REDIS_URL", "ttl_seconds": 604800},
    "slack": {
        "token_env": "SLACK_BOT_TOKEN",
        "approval_channel_env": "SLACK_APPROVAL_CHANNEL",
        "signing_secret_env": "SLACK_SIGNING_SECRET",
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

SAMPLE_PR_RESULT = PRResult(
    incident_id="inc-001",
    pr_url="https://github.com/acme/repo/pull/42",
    pr_number=42,
    branch_name="helix/fix-inc-001",
    iterations_taken=1,
    files_changed=["checkout.py"],
    fix_summary="Fixed the KeyError in checkout.",
)


@pytest.fixture(autouse=True)
def env_vars(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APPROVAL_CHANNEL", "C123")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "secret")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")


# ---------------------------------------------------------------------------
# handle_pr_created
# ---------------------------------------------------------------------------

async def test_handle_pr_created_posts_approval(monkeypatch):
    """When Slack is configured and PRResult exists, posts the approval request."""
    mock_redis = AsyncMock()
    post_approval = AsyncMock()

    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("agents.notifier.agent.read_pr_result", return_value=SAMPLE_PR_RESULT), \
         patch("agents.notifier.agent.slack.post_approval_request", post_approval):
        from agents.notifier.agent import handle_pr_created
        await handle_pr_created("inc-001", mock_redis)

    post_approval.assert_awaited_once()
    call_kwargs = post_approval.call_args.kwargs
    assert call_kwargs["incident_id"] == "inc-001"
    assert call_kwargs["pr_number"] == 42
    assert "checkout" in call_kwargs["fix_summary"].lower()


async def test_handle_pr_created_no_op_when_token_missing(monkeypatch, caplog):
    """No approval message and no error when SLACK_BOT_TOKEN is absent."""
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    mock_redis = AsyncMock()
    post_approval = AsyncMock()

    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("agents.notifier.agent.slack.post_approval_request", post_approval), \
         caplog.at_level(logging.WARNING, logger="agents.notifier.agent"):
        from agents.notifier.agent import handle_pr_created
        await handle_pr_created("inc-001", mock_redis)

    post_approval.assert_not_awaited()
    assert "SLACK_BOT_TOKEN" in caplog.text


async def test_handle_pr_created_no_op_when_channel_missing(monkeypatch, caplog):
    """No approval message and no error when SLACK_APPROVAL_CHANNEL is absent."""
    monkeypatch.delenv("SLACK_APPROVAL_CHANNEL", raising=False)
    mock_redis = AsyncMock()
    post_approval = AsyncMock()

    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("agents.notifier.agent.slack.post_approval_request", post_approval), \
         caplog.at_level(logging.WARNING, logger="agents.notifier.agent"):
        from agents.notifier.agent import handle_pr_created
        await handle_pr_created("inc-001", mock_redis)

    post_approval.assert_not_awaited()
    assert "SLACK_APPROVAL_CHANNEL" in caplog.text


async def test_handle_pr_created_logs_error_when_pr_result_missing(caplog):
    """Logs an error and does not raise when PRResult is not found in Redis."""
    mock_redis = AsyncMock()
    post_approval = AsyncMock()

    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("agents.notifier.agent.read_pr_result", return_value=None), \
         patch("agents.notifier.agent.slack.post_approval_request", post_approval), \
         caplog.at_level(logging.ERROR, logger="agents.notifier.agent"):
        from agents.notifier.agent import handle_pr_created
        await handle_pr_created("inc-missing", mock_redis)

    post_approval.assert_not_awaited()
    assert "pr_result not found" in caplog.text
