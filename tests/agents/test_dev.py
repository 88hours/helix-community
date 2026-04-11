"""Tests for agents/dev/agent.py"""
import asyncio
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
    r.get = AsyncMock(return_value=None)
    r.incr = AsyncMock(return_value=1)
    r.expire = AsyncMock(return_value=True)
    r.delete = AsyncMock(return_value=1)
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

TDD_PASSED = "TESTS_PASSED\nAdded a None guard before dereferencing user."
TDD_FAILED = "TESTS_FAILED\nAttempted to add a guard but the test still fails."


# ---------------------------------------------------------------------------
# handle() — pre-TDD steps (comment + event)
# ---------------------------------------------------------------------------

async def test_handle_posts_fix_comment(crash_report, qa_result, mock_redis, pr_result):
    """handle() must post the LLM fix suggestion as a GitHub Issue comment."""
    add_comment = AsyncMock()
    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("agents.dev.agent._fetch_source_files", new=AsyncMock(return_value={})), \
         patch("agents.dev.agent.complete", new=AsyncMock(return_value=LLM_FIX)), \
         patch("integrations.github.add_issue_comment", new=add_comment), \
         patch("agents.dev.agent._tdd_loop", new=AsyncMock(return_value=pr_result)):
        from agents.dev.agent import handle
        await handle(qa_result, crash_report, mock_redis)

    # The first add_issue_comment call is the fix suggestion.
    assert add_comment.await_count >= 1
    _, kwargs = add_comment.call_args_list[0]
    assert "Suggested fix" in kwargs["comment"]
    assert LLM_FIX in kwargs["comment"]


async def test_handle_publishes_fix_suggested_event(crash_report, qa_result, mock_redis, pr_result):
    """handle() must publish fix_suggested after posting the GitHub comment."""
    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("agents.dev.agent._fetch_source_files", new=AsyncMock(return_value={})), \
         patch("agents.dev.agent.complete", new=AsyncMock(return_value=LLM_FIX)), \
         patch("integrations.github.add_issue_comment", new=AsyncMock()), \
         patch("agents.dev.agent._tdd_loop", new=AsyncMock(return_value=pr_result)):
        from agents.dev.agent import handle
        await handle(qa_result, crash_report, mock_redis)

    mock_redis.publish.assert_called_once()
    channel = mock_redis.publish.call_args[0][0]
    assert "fix_suggested" in channel


async def test_handle_calls_tdd_loop(crash_report, qa_result, mock_redis, pr_result):
    """handle() must call _tdd_loop and return its PRResult."""
    tdd_loop = AsyncMock(return_value=pr_result)
    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("agents.dev.agent._fetch_source_files", new=AsyncMock(return_value={})), \
         patch("agents.dev.agent.complete", new=AsyncMock(return_value=LLM_FIX)), \
         patch("integrations.github.add_issue_comment", new=AsyncMock()), \
         patch("agents.dev.agent._tdd_loop", new=tdd_loop):
        from agents.dev.agent import handle
        result = await handle(qa_result, crash_report, mock_redis)

    tdd_loop.assert_awaited_once()
    assert result == pr_result


async def test_handle_escalates_on_timeout(crash_report, qa_result, mock_redis):
    """handle() must escalate and raise RuntimeError when the TDD loop times out."""
    escalate = AsyncMock()

    async def slow_tdd(**_kwargs):
        await asyncio.sleep(9999)

    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("agents.dev.agent._fetch_source_files", new=AsyncMock(return_value={})), \
         patch("agents.dev.agent.complete", new=AsyncMock(return_value=LLM_FIX)), \
         patch("integrations.github.add_issue_comment", new=AsyncMock()), \
         patch("agents.dev.agent._tdd_loop", new=slow_tdd), \
         patch("agents.dev.agent._TDD_TIMEOUT", 0.01), \
         patch("agents.dev.agent._escalate", new=escalate):
        from agents.dev.agent import handle
        with pytest.raises(RuntimeError, match="timed out"):
            await handle(qa_result, crash_report, mock_redis)

    escalate.assert_awaited_once()


# ---------------------------------------------------------------------------
# _tdd_loop()
# ---------------------------------------------------------------------------

async def test_tdd_loop_success_first_iteration(crash_report, qa_result, mock_redis):
    """_tdd_loop succeeds on the first iteration and returns a PRResult."""
    mock_redis.get.return_value = b"0"   # read_iterations → 0
    mock_redis.set.return_value = True   # lock acquired
    mock_redis.incr.return_value = 1     # iteration 1

    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("integrations.github.clone_repo", new=AsyncMock()), \
         patch("integrations.github.checkout_branch", new=AsyncMock()), \
         patch("integrations.github.write_file", new=AsyncMock()), \
         patch("integrations.github.commit_and_push", new=AsyncMock()), \
         patch("integrations.github.create_pull_request", new=AsyncMock(return_value=(7, "https://github.com/acme/repo/pull/7"))), \
         patch("agents.dev.agent.complete_tdd", new=AsyncMock(return_value=TDD_PASSED)), \
         patch("agents.dev.agent._get_changed_files", new=AsyncMock(return_value=["send_error.py"])), \
         patch("tempfile.mkdtemp", return_value="/tmp/helix-dev-test"), \
         patch("shutil.rmtree"):
        from agents.dev.agent import _tdd_loop
        result = await _tdd_loop(
            qa_result=qa_result,
            crash_report=crash_report,
            fix_suggestion=LLM_FIX,
            redis_client=mock_redis,
        )

    assert isinstance(result, PRResult)
    assert result.pr_number == 7
    assert result.iterations_taken == 1
    assert result.files_changed == ["send_error.py"]


async def test_tdd_loop_success_second_iteration(crash_report, qa_result, mock_redis):
    """_tdd_loop retries after a failure and succeeds on iteration 2."""
    mock_redis.get.return_value = b"0"
    mock_redis.set.return_value = True
    # First incr returns 1, second returns 2
    mock_redis.incr.side_effect = [1, 2]

    tdd_responses = [TDD_FAILED, TDD_PASSED]

    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("integrations.github.clone_repo", new=AsyncMock()), \
         patch("integrations.github.checkout_branch", new=AsyncMock()), \
         patch("integrations.github.write_file", new=AsyncMock()), \
         patch("integrations.github.commit_and_push", new=AsyncMock()), \
         patch("integrations.github.create_pull_request", new=AsyncMock(return_value=(8, "https://github.com/acme/repo/pull/8"))), \
         patch("agents.dev.agent.complete_tdd", new=AsyncMock(side_effect=tdd_responses)), \
         patch("agents.dev.agent._get_changed_files", new=AsyncMock(return_value=["send_error.py"])), \
         patch("tempfile.mkdtemp", return_value="/tmp/helix-dev-test"), \
         patch("shutil.rmtree"):
        from agents.dev.agent import _tdd_loop
        result = await _tdd_loop(
            qa_result=qa_result,
            crash_report=crash_report,
            fix_suggestion=LLM_FIX,
            redis_client=mock_redis,
        )

    assert result.iterations_taken == 2


async def test_tdd_loop_exhausts_all_iterations(crash_report, qa_result, mock_redis):
    """_tdd_loop raises RuntimeError and escalates after all 3 iterations fail."""
    mock_redis.get.return_value = b"0"
    mock_redis.set.return_value = True
    mock_redis.incr.side_effect = [1, 2, 3]

    post_failure = AsyncMock()
    escalate = AsyncMock()

    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("integrations.github.clone_repo", new=AsyncMock()), \
         patch("integrations.github.checkout_branch", new=AsyncMock()), \
         patch("integrations.github.write_file", new=AsyncMock()), \
         patch("agents.dev.agent.complete_tdd", new=AsyncMock(return_value=TDD_FAILED)), \
         patch("agents.dev.agent._post_failure_comment", new=post_failure), \
         patch("agents.dev.agent._escalate", new=escalate), \
         patch("tempfile.mkdtemp", return_value="/tmp/helix-dev-test"), \
         patch("shutil.rmtree"):
        from agents.dev.agent import _tdd_loop
        with pytest.raises(RuntimeError, match="exhausted all"):
            await _tdd_loop(
                qa_result=qa_result,
                crash_report=crash_report,
                fix_suggestion=LLM_FIX,
                redis_client=mock_redis,
            )

    post_failure.assert_awaited_once()
    escalate.assert_awaited_once()
    # All 3 failure explanations are recorded (prior_attempts is positional arg 2)
    prior_attempts_arg = post_failure.call_args.args[2]
    assert len(prior_attempts_arg) == 3


async def test_tdd_loop_already_exhausted(crash_report, qa_result, mock_redis):
    """_tdd_loop raises immediately when iteration counter is already at MAX."""
    mock_redis.get.return_value = b"3"  # already at MAX_ITERATIONS

    post_failure = AsyncMock()
    escalate = AsyncMock()

    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("agents.dev.agent._post_failure_comment", new=post_failure), \
         patch("agents.dev.agent._escalate", new=escalate):
        from agents.dev.agent import _tdd_loop
        with pytest.raises(RuntimeError, match="exhausted all"):
            await _tdd_loop(
                qa_result=qa_result,
                crash_report=crash_report,
                fix_suggestion=LLM_FIX,
                redis_client=mock_redis,
            )

    post_failure.assert_awaited_once()
    escalate.assert_awaited_once()


async def test_tdd_loop_repo_lock_not_acquired(crash_report, qa_result, mock_redis):
    """_tdd_loop raises RuntimeError when the repo lock cannot be acquired."""
    mock_redis.get.return_value = b"0"
    mock_redis.set.return_value = False  # lock never acquired

    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("agents.dev.agent._REPO_LOCK_RETRIES", 1), \
         patch("asyncio.sleep", new=AsyncMock()):
        from agents.dev.agent import _tdd_loop
        with pytest.raises(RuntimeError, match="Could not acquire repo lock"):
            await _tdd_loop(
                qa_result=qa_result,
                crash_report=crash_report,
                fix_suggestion=LLM_FIX,
                redis_client=mock_redis,
            )


async def test_tdd_loop_releases_lock_on_success(crash_report, qa_result, mock_redis):
    """_tdd_loop must release the repo lock even on success."""
    mock_redis.get.return_value = b"0"
    mock_redis.set.return_value = True
    mock_redis.incr.return_value = 1

    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("integrations.github.clone_repo", new=AsyncMock()), \
         patch("integrations.github.checkout_branch", new=AsyncMock()), \
         patch("integrations.github.write_file", new=AsyncMock()), \
         patch("integrations.github.commit_and_push", new=AsyncMock()), \
         patch("integrations.github.create_pull_request", new=AsyncMock(return_value=(7, "https://github.com/acme/repo/pull/7"))), \
         patch("agents.dev.agent.complete_tdd", new=AsyncMock(return_value=TDD_PASSED)), \
         patch("agents.dev.agent._get_changed_files", new=AsyncMock(return_value=[])), \
         patch("tempfile.mkdtemp", return_value="/tmp/helix-dev-test"), \
         patch("shutil.rmtree"):
        from agents.dev.agent import _tdd_loop
        await _tdd_loop(
            qa_result=qa_result,
            crash_report=crash_report,
            fix_suggestion=LLM_FIX,
            redis_client=mock_redis,
        )

    mock_redis.delete.assert_awaited_once()


async def test_tdd_loop_publishes_pr_created_event(crash_report, qa_result, mock_redis):
    """_tdd_loop must publish pr_created event on success."""
    mock_redis.get.return_value = b"0"
    mock_redis.set.return_value = True
    mock_redis.incr.return_value = 1

    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("integrations.github.clone_repo", new=AsyncMock()), \
         patch("integrations.github.checkout_branch", new=AsyncMock()), \
         patch("integrations.github.write_file", new=AsyncMock()), \
         patch("integrations.github.commit_and_push", new=AsyncMock()), \
         patch("integrations.github.create_pull_request", new=AsyncMock(return_value=(7, "https://github.com/acme/repo/pull/7"))), \
         patch("agents.dev.agent.complete_tdd", new=AsyncMock(return_value=TDD_PASSED)), \
         patch("agents.dev.agent._get_changed_files", new=AsyncMock(return_value=[])), \
         patch("tempfile.mkdtemp", return_value="/tmp/helix-dev-test"), \
         patch("shutil.rmtree"):
        from agents.dev.agent import _tdd_loop
        await _tdd_loop(
            qa_result=qa_result,
            crash_report=crash_report,
            fix_suggestion=LLM_FIX,
            redis_client=mock_redis,
        )

    published_channels = [call[0][0] for call in mock_redis.publish.call_args_list]
    assert any("pr_created" in ch for ch in published_channels)


# ---------------------------------------------------------------------------
# _tests_passed / _extract_explanation
# ---------------------------------------------------------------------------

def test_tests_passed_detects_sentinel():
    from agents.dev.agent import _tests_passed
    assert _tests_passed("some output\nTESTS_PASSED\nFixed the bug.") is True
    assert _tests_passed("TESTS_PASSED") is True


def test_tests_passed_returns_false_on_failure_sentinel():
    from agents.dev.agent import _tests_passed
    assert _tests_passed("TESTS_FAILED\nIt still crashes.") is False


def test_tests_passed_returns_false_on_empty():
    from agents.dev.agent import _tests_passed
    assert _tests_passed("") is False
    assert _tests_passed("no sentinel here") is False


def test_extract_explanation_after_passed():
    from agents.dev.agent import _extract_explanation
    result = _extract_explanation("preamble\nTESTS_PASSED\nAdded a None guard.")
    assert result == "Added a None guard."


def test_extract_explanation_after_failed():
    from agents.dev.agent import _extract_explanation
    result = _extract_explanation("TESTS_FAILED\nThe approach was wrong.")
    assert result == "The approach was wrong."


def test_extract_explanation_no_sentinel_returns_full():
    from agents.dev.agent import _extract_explanation
    result = _extract_explanation("some random output")
    assert result == "some random output"


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
