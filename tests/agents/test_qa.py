"""Tests for agents/qa/agent.py"""
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from core.models import (
    CrashReport,
    QAResult,
    Severity,
    TicketAction,
)


SAMPLE_YAML = {
    "rollbar": {"access_token_env": "ROLLBAR_ACCESS_TOKEN"},
    "redis": {"url_env": "REDIS_URL", "ttl_seconds": 604800},
    "agents": {
        "qa": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
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
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APPROVAL_CHANNEL", "C123")


@pytest.fixture
def crash_report():
    return CrashReport(
        incident_id="inc-001",
        source_item_id="12345", source="rollbar",
        severity=Severity.high,
        error_type="KeyError",
        error_message="'item_id'",
        stack_trace='File "checkout.py", line 42, in process\n    item = cart[item_id]',
        affected_component="checkout",
        affected_endpoint="/api/v1/checkout",
        summary="A KeyError in the checkout process.",
        language="python",
    )


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.set = AsyncMock(return_value=True)
    r.publish = AsyncMock(return_value=1)
    r.xadd = AsyncMock(return_value=b"1234567890-0")
    return r


LLM_RESPONSE = json.dumps({
    "file_path": "tests/test_checkout.py",
    "test_name": "test_checkout_raises_on_missing_item",
    "content": "def test_checkout_raises_on_missing_item():\n    assert False",
})


# ---------------------------------------------------------------------------
# handle()
# ---------------------------------------------------------------------------

async def test_handle_returns_qa_result(crash_report, mock_redis):
    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("integrations.github.clone_repo", new=AsyncMock()), \
         patch("integrations.github.find_existing_issue", new=AsyncMock(return_value=None)), \
         patch("integrations.github.create_issue", new=AsyncMock(return_value=("42", "https://github.com/acme/repo/issues/42"))), \
         patch("integrations.github.add_issue_comment", new=AsyncMock()), \
         patch("agents.qa.agent.complete", new=AsyncMock(return_value=LLM_RESPONSE)):
        from agents.qa.agent import handle
        result = await handle(crash_report, mock_redis)

    assert isinstance(result, QAResult)
    assert result.incident_id == "inc-001"
    assert result.test_case.file_path == "tests/test_checkout.py"
    assert result.ticket_action == TicketAction.created


async def test_handle_updates_existing_issue(crash_report, mock_redis):
    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("integrations.github.clone_repo", new=AsyncMock()), \
         patch("integrations.github.find_existing_issue", new=AsyncMock(return_value=("42", "https://github.com/acme/repo/issues/42"))), \
         patch("integrations.github.add_issue_comment", new=AsyncMock()), \
         patch("agents.qa.agent.complete", new=AsyncMock(return_value=LLM_RESPONSE)):
        from agents.qa.agent import handle
        result = await handle(crash_report, mock_redis)

    assert result.ticket_id == "42"
    assert result.ticket_action == TicketAction.updated


async def test_handle_publishes_event(crash_report, mock_redis):
    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("integrations.github.clone_repo", new=AsyncMock()), \
         patch("integrations.github.find_existing_issue", new=AsyncMock(return_value=None)), \
         patch("integrations.github.create_issue", new=AsyncMock(return_value=("42", "https://github.com/acme/repo/issues/42"))), \
         patch("integrations.github.add_issue_comment", new=AsyncMock()), \
         patch("agents.qa.agent.complete", new=AsyncMock(return_value=LLM_RESPONSE)):
        from agents.qa.agent import handle
        await handle(crash_report, mock_redis)

    mock_redis.publish.assert_called_once()
    channel = mock_redis.publish.call_args[0][0]
    assert "test_case_generated" in channel


async def test_handle_posts_test_case_comment(crash_report, mock_redis):
    add_comment = AsyncMock()
    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("integrations.github.clone_repo", new=AsyncMock()), \
         patch("integrations.github.find_existing_issue", new=AsyncMock(return_value=None)), \
         patch("integrations.github.create_issue", new=AsyncMock(return_value=("42", "https://github.com/acme/repo/issues/42"))), \
         patch("integrations.github.add_issue_comment", new=add_comment), \
         patch("agents.qa.agent.complete", new=AsyncMock(return_value=LLM_RESPONSE)):
        from agents.qa.agent import handle
        await handle(crash_report, mock_redis)

    # add_issue_comment is called once for the test-case comment
    # (the create path does not call it for the initial re-detection notice)
    add_comment.assert_called_once()
    _, kwargs = add_comment.call_args
    assert "test_checkout_raises_on_missing_item" in kwargs["comment"]
    assert "```python" in kwargs["comment"]


# ---------------------------------------------------------------------------
# _extract_paths_from_stack_trace
# ---------------------------------------------------------------------------

def test_extract_paths_basic():
    from agents.qa.agent import _extract_paths_from_stack_trace
    trace = 'File "checkout.py", line 42, in process'
    paths = _extract_paths_from_stack_trace(trace)
    assert "checkout.py" in paths


def test_extract_paths_filters_stdlib():
    from agents.qa.agent import _extract_paths_from_stack_trace
    trace = 'File "/usr/lib/python3.12/json/decoder.py", line 10, in decode'
    paths = _extract_paths_from_stack_trace(trace)
    assert paths == []


def test_extract_paths_filters_site_packages():
    from agents.qa.agent import _extract_paths_from_stack_trace
    trace = 'File "/usr/local/lib/python3.12/site-packages/pydantic/main.py", line 1, in foo'
    paths = _extract_paths_from_stack_trace(trace)
    assert paths == []


def test_extract_paths_deduplicates():
    from agents.qa.agent import _extract_paths_from_stack_trace
    trace = (
        'File "checkout.py", line 1, in a\n'
        'File "checkout.py", line 2, in b'
    )
    paths = _extract_paths_from_stack_trace(trace)
    assert paths.count("checkout.py") == 1


def test_extract_paths_most_recent_first():
    from agents.qa.agent import _extract_paths_from_stack_trace
    trace = (
        'File "module_a.py", line 1, in a\n'
        'File "module_b.py", line 2, in b'
    )
    paths = _extract_paths_from_stack_trace(trace)
    assert paths[0] == "module_b.py"


# ---------------------------------------------------------------------------
# _read_relevant_files
# ---------------------------------------------------------------------------

def test_read_relevant_files_reads_existing_file(tmp_path):
    from agents.qa.agent import _read_relevant_files
    (tmp_path / "checkout.py").write_text("def process(): pass")
    trace = 'File "checkout.py", line 1, in process'
    files = _read_relevant_files(str(tmp_path), trace)
    assert "checkout.py" in files
    assert "process" in files["checkout.py"]


def test_read_relevant_files_skips_missing_file(tmp_path):
    from agents.qa.agent import _read_relevant_files
    trace = 'File "nonexistent.py", line 1, in fn'
    files = _read_relevant_files(str(tmp_path), trace)
    assert files == {}


def test_read_relevant_files_truncates_large_file(tmp_path):
    from agents.qa.agent import _read_relevant_files, _MAX_FILE_CHARS
    big_content = "x" * (_MAX_FILE_CHARS + 1000)
    (tmp_path / "big.py").write_text(big_content)
    trace = 'File "big.py", line 1, in fn'
    files = _read_relevant_files(str(tmp_path), trace)
    assert "[truncated]" in files["big.py"]


def test_read_relevant_files_respects_max_count(tmp_path):
    from agents.qa.agent import _read_relevant_files, _MAX_SOURCE_FILES
    # Create more files than the limit
    trace_lines = []
    for i in range(_MAX_SOURCE_FILES + 3):
        fname = f"module_{i}.py"
        (tmp_path / fname).write_text("pass")
        trace_lines.append(f'File "{fname}", line 1, in fn')
    trace = "\n".join(trace_lines)
    files = _read_relevant_files(str(tmp_path), trace)
    assert len(files) <= _MAX_SOURCE_FILES


# ---------------------------------------------------------------------------
# _check_test
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# _extract_paths_from_stack_trace — multi-language
# ---------------------------------------------------------------------------

def test_extract_paths_javascript():
    from agents.qa.agent import _extract_paths_from_stack_trace
    trace = "at process (/app/src/checkout.js:10:5)\nat Object.<anonymous> (/app/src/app.js:5:3)"
    paths = _extract_paths_from_stack_trace(trace, language="javascript")
    assert any("checkout.js" in p for p in paths)
    assert any("app.js" in p for p in paths)


def test_extract_paths_javascript_filters_node_modules():
    from agents.qa.agent import _extract_paths_from_stack_trace
    trace = "at fn (/app/node_modules/express/lib/router.js:10:5)"
    paths = _extract_paths_from_stack_trace(trace, language="javascript")
    assert paths == []


def test_extract_paths_ruby():
    from agents.qa.agent import _extract_paths_from_stack_trace
    trace = "/app/lib/checkout.rb:42:in `process'"
    paths = _extract_paths_from_stack_trace(trace, language="ruby")
    assert any("checkout.rb" in p for p in paths)


def test_extract_paths_ruby_filters_gems():
    from agents.qa.agent import _extract_paths_from_stack_trace
    trace = "/usr/local/bundle/gems/rails-7.0.0/lib/router.rb:10:in `call'"
    paths = _extract_paths_from_stack_trace(trace, language="ruby")
    assert paths == []


def test_extract_paths_go():
    from agents.qa.agent import _extract_paths_from_stack_trace
    trace = "/home/user/app/checkout/processor.go:42 +0x1234"
    paths = _extract_paths_from_stack_trace(trace, language="go")
    assert any("processor.go" in p for p in paths)


def test_extract_paths_go_filters_stdlib():
    from agents.qa.agent import _extract_paths_from_stack_trace
    trace = "/usr/local/go/src/runtime/panic.go:965 +0x1b6"
    paths = _extract_paths_from_stack_trace(trace, language="go")
    assert paths == []


def test_extract_paths_java():
    from agents.qa.agent import _extract_paths_from_stack_trace
    trace = "at com.example.checkout.Processor.process(Processor.java:42)"
    paths = _extract_paths_from_stack_trace(trace, language="java")
    assert "Processor.java" in paths


def test_extract_paths_java_filters_jdk():
    from agents.qa.agent import _extract_paths_from_stack_trace
    trace = "at java.lang.reflect.Method.invoke(Method.java:498)"
    paths = _extract_paths_from_stack_trace(trace, language="java")
    assert paths == []


# ---------------------------------------------------------------------------
# _check_test — multi-language anti-pattern detection
# ---------------------------------------------------------------------------

def test_check_test_rejects_pytest_raises_with_crash_error():
    from agents.qa.agent import _check_test
    bad_test = (
        "import pytest\n"
        "from greet import greet_user\n\n"
        "def test_greet_user_raises():\n"
        "    with pytest.raises(AttributeError):\n"
        "        greet_user('bob')\n"
    )
    problem = _check_test(bad_test, "AttributeError", language="python")
    assert problem != ""
    assert "pytest.raises" in problem


def test_check_test_accepts_correct_behaviour_assertion():
    from agents.qa.agent import _check_test
    good_test = (
        "from greet import greet_user\n\n"
        "def test_greet_user_returns_none_for_unknown():\n"
        "    result = greet_user('bob')\n"
        "    assert result is None\n"
    )
    assert _check_test(good_test, "AttributeError", language="python") == ""


def test_check_test_allows_raises_for_different_exception():
    from agents.qa.agent import _check_test
    # pytest.raises(ValueError) is fine when the crash was AttributeError
    test = (
        "import pytest\n"
        "from greet import greet_user\n\n"
        "def test_greet_user_raises_value_error_on_empty():\n"
        "    with pytest.raises(ValueError):\n"
        "        greet_user('')\n"
    )
    assert _check_test(test, "AttributeError", language="python") == ""


def test_check_test_rejects_jest_to_throw():
    from agents.qa.agent import _check_test
    bad_test = (
        "test('throws TypeError', () => {\n"
        "  expect(() => process(null)).toThrow(TypeError);\n"
        "});\n"
    )
    problem = _check_test(bad_test, "TypeError", language="javascript")
    assert problem != ""
    assert "toThrow" in problem


def test_check_test_accepts_jest_return_assertion():
    from agents.qa.agent import _check_test
    good_test = (
        "test('returns null for missing item', () => {\n"
        "  const result = process(null);\n"
        "  expect(result).toBeNull();\n"
        "});\n"
    )
    assert _check_test(good_test, "TypeError", language="javascript") == ""


def test_check_test_rejects_rspec_raise_error():
    from agents.qa.agent import _check_test
    bad_test = (
        "it 'raises RuntimeError' do\n"
        "  expect { process(nil) }.to raise_error(RuntimeError)\n"
        "end\n"
    )
    problem = _check_test(bad_test, "RuntimeError", language="ruby")
    assert problem != ""
    assert "raise_error" in problem


def test_check_test_rejects_junit_assert_throws():
    from agents.qa.agent import _check_test
    bad_test = (
        "@Test\n"
        "public void testThrows() {\n"
        "  assertThrows(NullPointerException.class, () -> process(null));\n"
        "}\n"
    )
    problem = _check_test(bad_test, "NullPointerException", language="java")
    assert problem != ""


async def test_handle_retries_when_test_fails_validation(crash_report, mock_redis):
    """handle() must retry the LLM call when _check_test flags the response."""
    bad_response = json.dumps({
        "file_path": "tests/test_checkout.py",
        "test_name": "test_checkout_raises",
        "content": "import pytest\ndef test_checkout_raises():\n    with pytest.raises(KeyError):\n        pass",
    })
    good_response = json.dumps({
        "file_path": "tests/test_checkout.py",
        "test_name": "test_checkout_returns_error",
        "content": "def test_checkout_returns_error():\n    result = checkout(None)\n    assert result is None",
    })
    # First call returns bad test, second returns good test
    complete_mock = AsyncMock(side_effect=[bad_response, good_response])

    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("integrations.github.clone_repo", new=AsyncMock()), \
         patch("integrations.github.find_existing_issue", new=AsyncMock(return_value=None)), \
         patch("integrations.github.create_issue", new=AsyncMock(return_value=("42", "https://github.com/acme/repo/issues/42"))), \
         patch("integrations.github.add_issue_comment", new=AsyncMock()), \
         patch("agents.qa.agent.complete", new=complete_mock):
        from agents.qa.agent import handle
        result = await handle(crash_report, mock_redis)

    assert complete_mock.await_count == 2
    assert result.test_case.test_name == "test_checkout_returns_error"


# ---------------------------------------------------------------------------
# Duplicate detection — early return
# ---------------------------------------------------------------------------

async def test_handle_duplicate_skips_pipeline(crash_report, mock_redis):
    """When an existing issue is found, handle() must return early without
    cloning the repo, generating a test case, or publishing test_case_generated."""
    clone_mock = AsyncMock()
    complete_mock = AsyncMock()

    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("integrations.github.find_existing_issue", new=AsyncMock(return_value=("42", "https://github.com/acme/repo/issues/42"))), \
         patch("integrations.github.add_issue_comment", new=AsyncMock()), \
         patch("integrations.github.clone_repo", clone_mock), \
         patch("agents.qa.agent.complete", complete_mock):
        from agents.qa.agent import handle
        result = await handle(crash_report, mock_redis)

    # Repo must NOT be cloned and LLM must NOT be called
    clone_mock.assert_not_awaited()
    complete_mock.assert_not_awaited()

    # Result must carry the duplicate ticket info
    assert result.ticket_id == "42"
    assert result.ticket_action == TicketAction.updated

    # duplicate_detected event must be published
    channels = [call.args[0] for call in mock_redis.publish.call_args_list]
    assert any("duplicate_detected" in c for c in channels)

    # test_case_generated must NOT be published
    assert not any("test_case_generated" in c for c in channels)


async def test_handle_duplicate_notification_sent(crash_report, mock_redis):
    """handle_duplicate() sends a Slack message when Slack is configured."""
    post_message_mock = AsyncMock()

    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("agents.notifier.agent.slack.post_message", post_message_mock):
        from agents.notifier.agent import handle_duplicate
        await handle_duplicate(
            incident_id="inc-001",
            issue_url="https://github.com/acme/repo/issues/42",
            error_type="KeyError",
            error_message="'item_id'",
            redis_client=mock_redis,
        )

    post_message_mock.assert_awaited_once()
    text = post_message_mock.call_args.kwargs["text"]
    assert "recurring" in text.lower() or "recur" in text.lower()
    assert "https://github.com/acme/repo/issues/42" in text


async def test_handle_duplicate_no_op_when_slack_not_configured(crash_report, mock_redis, monkeypatch):
    """handle_duplicate() is silent when Slack is not configured."""
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    post_message_mock = AsyncMock()

    with patch("core.config._load_yaml", return_value=SAMPLE_YAML), \
         patch("agents.notifier.agent.slack.post_message", post_message_mock):
        from agents.notifier.agent import handle_duplicate
        await handle_duplicate(
            incident_id="inc-001",
            issue_url="https://github.com/acme/repo/issues/42",
            error_type="KeyError",
            error_message="'item_id'",
            redis_client=mock_redis,
        )

    post_message_mock.assert_not_awaited()
