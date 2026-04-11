"""Tests for core/state.py"""
import pytest
from unittest.mock import AsyncMock

from core.models import CrashReport, PRResult, QAResult, Severity, TestCase, TestFormat, TicketAction
from core.state import (
    increment_iterations,
    read_crash_report,
    read_iterations,
    read_pr_result,
    read_qa_result,
    read_status,
    write_crash_report,
    write_pr_result,
    write_qa_result,
    write_status,
)


@pytest.fixture
def redis():
    return AsyncMock()


@pytest.fixture
def crash_report():
    return CrashReport(
        incident_id="inc-001",
        source_item_id="12345", source="rollbar",
        severity=Severity.high,
        error_type="KeyError",
        error_message="missing key",
        stack_trace="...",
        affected_component="checkout",
        affected_endpoint="/checkout",
        summary="A KeyError occurred.",
    )


@pytest.fixture
def qa_result():
    return QAResult(
        incident_id="inc-001",
        ticket_id="PROJ-1",
        ticket_url="https://jira.example.com/browse/PROJ-1",
        ticket_action=TicketAction.created,
        test_case=TestCase(
            file_path="tests/test_checkout.py",
            test_name="test_foo",
            content="def test_foo(): pass",
            format=TestFormat.pytest,
        ),
    )


@pytest.fixture
def pr_result():
    return PRResult(
        incident_id="inc-001",
        pr_url="https://github.com/acme/repo/pull/1",
        pr_number=1,
        branch_name="helix/fix/abc12345-1",
        iterations_taken=1,
        fix_summary="Fixed the KeyError.",
    )


# ---------------------------------------------------------------------------
# CrashReport
# ---------------------------------------------------------------------------

async def test_write_crash_report(redis, crash_report):
    await write_crash_report(redis, crash_report)
    redis.set.assert_awaited_once()
    key = redis.set.call_args[0][0]
    assert "inc-001" in key
    assert "crash_report" in key


async def test_read_crash_report(redis, crash_report):
    redis.get.return_value = crash_report.model_dump_json().encode()
    result = await read_crash_report(redis, "inc-001")
    assert result.incident_id == "inc-001"
    assert result.severity == Severity.high


async def test_read_crash_report_missing(redis):
    redis.get.return_value = None
    result = await read_crash_report(redis, "inc-001")
    assert result is None


# ---------------------------------------------------------------------------
# QAResult
# ---------------------------------------------------------------------------

async def test_write_qa_result(redis, qa_result):
    await write_qa_result(redis, qa_result)
    redis.set.assert_awaited_once()
    assert "test_case" in redis.set.call_args[0][0]


async def test_read_qa_result(redis, qa_result):
    redis.get.return_value = qa_result.model_dump_json().encode()
    result = await read_qa_result(redis, "inc-001")
    assert result.ticket_id == "PROJ-1"


async def test_read_qa_result_missing(redis):
    redis.get.return_value = None
    assert await read_qa_result(redis, "inc-001") is None


# ---------------------------------------------------------------------------
# PRResult
# ---------------------------------------------------------------------------

async def test_write_pr_result(redis, pr_result):
    await write_pr_result(redis, pr_result)
    redis.set.assert_awaited_once()
    assert ":pr" in redis.set.call_args[0][0]


async def test_read_pr_result(redis, pr_result):
    redis.get.return_value = pr_result.model_dump_json().encode()
    result = await read_pr_result(redis, "inc-001")
    assert result.pr_number == 1


async def test_read_pr_result_missing(redis):
    redis.get.return_value = None
    assert await read_pr_result(redis, "inc-001") is None


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

async def test_write_and_read_status(redis):
    await write_status(redis, "inc-001", "crash_analysed")
    redis.set.assert_awaited_once()

    redis.get.return_value = b"crash_analysed"
    status = await read_status(redis, "inc-001")
    assert status == "crash_analysed"


async def test_read_status_returns_string_when_already_str(redis):
    redis.get.return_value = "crash_analysed"
    status = await read_status(redis, "inc-001")
    assert status == "crash_analysed"


async def test_read_status_missing(redis):
    redis.get.return_value = None
    assert await read_status(redis, "inc-001") is None


# ---------------------------------------------------------------------------
# Iteration counter
# ---------------------------------------------------------------------------

async def test_increment_iterations_returns_new_count(redis):
    redis.incr.return_value = 1
    result = await increment_iterations(redis, "inc-001")
    assert result == 1
    redis.incr.assert_awaited_once()
    key = redis.incr.call_args[0][0]
    assert "inc-001" in key
    assert "iterations" in key


async def test_increment_iterations_sets_ttl(redis):
    redis.incr.return_value = 2
    await increment_iterations(redis, "inc-001")
    redis.expire.assert_awaited_once()


async def test_increment_iterations_sequential(redis):
    redis.incr.side_effect = [1, 2, 3]
    assert await increment_iterations(redis, "inc-001") == 1
    assert await increment_iterations(redis, "inc-001") == 2
    assert await increment_iterations(redis, "inc-001") == 3


async def test_read_iterations_returns_count(redis):
    redis.get.return_value = b"2"
    result = await read_iterations(redis, "inc-001")
    assert result == 2


async def test_read_iterations_missing_returns_zero(redis):
    redis.get.return_value = None
    result = await read_iterations(redis, "inc-001")
    assert result == 0
