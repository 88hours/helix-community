"""Tests for agents/dev/agent.py"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.models import (
    CrashReport,
    PRResult,
    QAResult,
    Severity,
    TestCase,
    TestFormat,
    TicketAction,
)


SAMPLE_YAML = {
    "rollbar": {"access_token_env": "ROLLBAR_ACCESS_TOKEN"},
    "redis": {"url_env": "REDIS_URL", "ttl_seconds": 604800},
    "agents": {
        "dev": {"provider": "anthropic", "model": "claude-sonnet-4-6"},
    },
    "github": {
        "token_env": "GITHUB_TOKEN",
        "target_repo": "acme/repo",
        "base_branch": "main",
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
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APPROVAL_CHANNEL", "C123")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "signing-secret")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("EMAIL_FROM", "helix@acme.com")
    monkeypatch.setenv("EMAIL_TO", "oncall@acme.com")
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "user")
    monkeypatch.setenv("SMTP_PASSWORD", "pass")


@pytest.fixture
def crash_report():
    return CrashReport(
        incident_id="inc-001",
        source_item_id="12345", source="rollbar",
        severity=Severity.high,
        error_type="AttributeError",
        error_message="'NoneType' object has no attribute 'get'",
        stack_trace="File send_error.py line 25 in greet_user",
        affected_component="greet_user",
        affected_endpoint="/api/greet",
        summary="greet_user crashes when user is None.",
    )


@pytest.fixture
def qa_result():
    return QAResult(
        incident_id="inc-001",
        ticket_id="42",
        ticket_url="https://github.com/acme/repo/issues/42",
        ticket_action=TicketAction.created,
        test_case=TestCase(
            file_path="tests/test_greet.py",
            test_name="test_greet_user_missing",
            content="def test_greet_user_missing():\n    assert greet_user('bob') is not None",
            format=TestFormat.pytest,
        ),
        relevant_files=["send_error.py"],
    )


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.set = AsyncMock(return_value=True)
    r.publish = AsyncMock(return_value=1)
    r.xadd = AsyncMock(return_value=b"1234567890-0")
    return r


@pytest.fixture
def pr_result():
    return PRResult(
        incident_id="inc-001",
        pr_url="https://github.com/acme/repo/pull/7",
        pr_number=7,
        branch_name="helix/fix/inc-001-1",
        iterations_taken=1,
        files_changed=["send_error.py"],
        fix_summary="Added None guard in greet_user.",
    )


LLM_FIX = (
    "**Root cause:** `get_user` returns None for unknown users.\n\n"
    "```python\n# Before\nreturn f\"Hello, {user.get('name')}!\"\n"
    "# After\nif user is None:\n    return 'Unknown user'\n"
    "return f\"Hello, {user.get('name')}!\"\n```"
)


# ---------------------------------------------------------------------------
# handle() — pre-TDD steps (comment + event); TDD loop is mocked out
# ---------------------------------------------------------------------------

async def test_handle_posts_fix_comment(crash_report, qa_result, mock_redis):
    """handle() must post the LLM fix suggestion as a GitHub Issue comment."""
    add_comment = AsyncMock()
    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("agents.dev.agent._fetch_source_files", new=AsyncMock(return_value={})), \
         patch("agents.dev.agent.complete", new=AsyncMock(return_value=LLM_FIX)), \
         patch("integrations.github.add_issue_comment", new=add_comment):
        from agents.dev.agent import handle
        await handle(qa_result, crash_report, mock_redis)

    add_comment.assert_awaited_once()
    _, kwargs = add_comment.call_args
    assert "Suggested fix" in kwargs["comment"]
    assert LLM_FIX in kwargs["comment"]


async def test_handle_publishes_fix_suggested_event(crash_report, qa_result, mock_redis):
    """handle() must publish fix_suggested after posting the GitHub comment."""
    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("agents.dev.agent._fetch_source_files", new=AsyncMock(return_value={})), \
         patch("agents.dev.agent.complete", new=AsyncMock(return_value=LLM_FIX)), \
         patch("integrations.github.add_issue_comment", new=AsyncMock()):
        from agents.dev.agent import handle
        await handle(qa_result, crash_report, mock_redis)

    mock_redis.publish.assert_called_once()
    channel = mock_redis.publish.call_args[0][0]
    assert "fix_suggested" in channel


# ---------------------------------------------------------------------------
# _fetch_source_files
# ---------------------------------------------------------------------------

async def test_fetch_source_files_returns_content():
    import base64
    fake_content = base64.b64encode(b"def get_user(): pass").decode()

    mock_response = MagicMock()
    mock_response.json.return_value = {"content": fake_content + "\n"}
    mock_response.raise_for_status.return_value = None

    with patch("agents.dev.agent.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(return_value=mock_response)

        from agents.dev.agent import _fetch_source_files
        result = await _fetch_source_files("acme/repo", ["send_error.py"])

    assert "send_error.py" in result
    assert "get_user" in result["send_error.py"]


async def test_fetch_source_files_skips_missing():
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = Exception("404")

    with patch("agents.dev.agent.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(return_value=mock_response)

        from agents.dev.agent import _fetch_source_files
        result = await _fetch_source_files("acme/repo", ["nonexistent.py"])

    assert result == {}
