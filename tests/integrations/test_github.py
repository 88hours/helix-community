"""Tests for integrations/github.py"""
import pytest
import respx
import httpx
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from integrations import github


def _make_proc(returncode: int = 0, stdout: bytes = b"ok", stderr: bytes = b""):
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate.return_value = (stdout, stderr)
    return proc


# ---------------------------------------------------------------------------
# _git helper
# ---------------------------------------------------------------------------

async def test_git_success():
    proc = _make_proc(stdout=b"main\n")
    with patch("asyncio.create_subprocess_exec", return_value=proc):
        result = await github._git(["branch"])
    assert result == "main"


async def test_git_failure_raises():
    proc = _make_proc(returncode=1, stderr=b"fatal: not a git repo")
    with patch("asyncio.create_subprocess_exec", return_value=proc):
        with pytest.raises(RuntimeError, match="failed"):
            await github._git(["status"])


# ---------------------------------------------------------------------------
# clone_repo
# ---------------------------------------------------------------------------

async def test_clone_repo_without_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    proc = _make_proc()
    with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
        await github.clone_repo("https://github.com/acme/repo.git", "/tmp/repo")
    args = mock_exec.call_args[0]
    assert "https://github.com/acme/repo.git" in args


async def test_clone_repo_with_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    proc = _make_proc()
    with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
        await github.clone_repo("https://github.com/acme/repo.git", "/tmp/repo")
    args = mock_exec.call_args[0]
    assert "ghp_test@" in " ".join(str(a) for a in args)


# ---------------------------------------------------------------------------
# checkout_branch
# ---------------------------------------------------------------------------

async def test_checkout_branch():
    proc = _make_proc()
    with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
        await github.checkout_branch("/tmp/repo", "helix/fix/abc-1")
    args = mock_exec.call_args[0]
    assert "helix/fix/abc-1" in args


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------

async def test_write_file(tmp_path):
    await github.write_file(str(tmp_path), "tests/test_foo.py", "def test_foo(): pass")
    written = (tmp_path / "tests" / "test_foo.py").read_text()
    assert "test_foo" in written


async def test_write_file_creates_parent_dirs(tmp_path):
    await github.write_file(str(tmp_path), "a/b/c/file.py", "content")
    assert (tmp_path / "a" / "b" / "c" / "file.py").exists()


# ---------------------------------------------------------------------------
# commit_and_push
# ---------------------------------------------------------------------------

async def test_commit_and_push():
    proc = _make_proc()
    with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
        await github.commit_and_push("/tmp/repo", "helix/fix/abc-1", "fix: bug")
    # Should call: config user.name, config user.email, add, commit, push
    assert mock_exec.call_count == 5


# ---------------------------------------------------------------------------
# create_pull_request
# ---------------------------------------------------------------------------

@respx.mock
async def test_create_pull_request(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    respx.post("https://api.github.com/repos/acme/repo/pulls").mock(
        return_value=httpx.Response(201, json={"number": 42, "html_url": "https://github.com/acme/repo/pull/42"})
    )
    pr_number, pr_url = await github.create_pull_request(
        repo="acme/repo", title="Fix bug", body="details", head="helix/fix", base="main"
    )
    assert pr_number == 42
    assert "pull/42" in pr_url


# ---------------------------------------------------------------------------
# merge_pull_request
# ---------------------------------------------------------------------------

@respx.mock
async def test_merge_pull_request(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    respx.put("https://api.github.com/repos/acme/repo/pulls/42/merge").mock(
        return_value=httpx.Response(200, json={"merged": True})
    )
    await github.merge_pull_request("acme/repo", 42, commit_title="fix: squash")


@respx.mock
async def test_merge_pull_request_no_title(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    respx.put("https://api.github.com/repos/acme/repo/pulls/1/merge").mock(
        return_value=httpx.Response(200, json={"merged": True})
    )
    await github.merge_pull_request("acme/repo", 1)  # no commit_title


# ---------------------------------------------------------------------------
# get_pr_diff
# ---------------------------------------------------------------------------

@respx.mock
async def test_get_pr_diff(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    respx.get("https://api.github.com/repos/acme/repo/pulls/42").mock(
        return_value=httpx.Response(200, text="diff --git a/foo.py b/foo.py\n+fix")
    )
    diff = await github.get_pr_diff("acme/repo", 42)
    assert "diff --git" in diff


# ---------------------------------------------------------------------------
# _api_headers missing token
# ---------------------------------------------------------------------------

def test_api_headers_missing_token_raises(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(EnvironmentError, match="GITHUB_TOKEN"):
        github._api_headers()
