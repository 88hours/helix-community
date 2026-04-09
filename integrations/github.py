"""
GitHub integration for the Helix agent pipeline.

Provides thin async wrappers around the GitHub REST API and git CLI:

  clone_repo          — clone a repository to a local temp directory
  checkout_branch     — create and switch to a new branch
  write_file          — write a file to the local clone
  commit_and_push     — stage all changes, commit, and push the branch
  create_pull_request — open a PR via the GitHub API
  get_pr_diff         — fetch the unified diff of a PR for review

Git operations use subprocess (the standard approach for repo manipulation).
GitHub API calls use httpx (async).

Required environment variable:
    GITHUB_TOKEN  — Personal access token or GitHub App token with repo scope.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"


# ---------------------------------------------------------------------------
# Git CLI helpers (subprocess)
# ---------------------------------------------------------------------------

async def _git(args: list[str], cwd: Optional[str] = None) -> str:
    """
    Run a git command and return its stdout.

    Args:
        args: Arguments to pass to git (without the "git" prefix).
        cwd:  Working directory for the command. Defaults to the current dir.

    Returns:
        Decoded stdout from the git process.

    Raises:
        RuntimeError: If git exits with a non-zero return code.
    """
    process = await asyncio.create_subprocess_exec(
        "git", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {process.returncode}): "
            f"{stderr.decode().strip()}"
        )
    return stdout.decode().strip()


async def clone_repo(repo_url: str, target_dir: str, token: str | None = None) -> None:
    """
    Clone a GitHub repository to a local directory.

    If a token is provided (or GITHUB_TOKEN is set), it is embedded in the URL
    so private repos work.

    Args:
        repo_url:   HTTPS clone URL, e.g. "https://github.com/org/repo.git"
        target_dir: Local path to clone into. Must not already exist.
        token:      GitHub token. Falls back to GITHUB_TOKEN env var.
    """
    if token:
        resolved = token
    else:
        logger.warning(
            "no installation token provided for clone — falling back to GITHUB_TOKEN env var; "
            "ideally a GitHub App installation token should be used"
        )
        resolved = os.environ.get("GITHUB_TOKEN")
    if resolved and repo_url.startswith("https://github.com/"):
        # Embed token using x-access-token as the username. This works for both
        # classic PATs and GitHub App installation tokens (ghs_...). Using the
        # token as the username alone causes git to prompt for a password on
        # App tokens, which fails in a headless environment.
        repo_url = repo_url.replace("https://", f"https://x-access-token:{resolved}@")

    await _git(["clone", "--depth", "1", repo_url, target_dir])
    logger.info("repo cloned", extra={"target_dir": target_dir})


async def checkout_branch(repo_dir: str, branch_name: str) -> None:
    """
    Create and switch to a new branch in the local clone.

    Args:
        repo_dir:    Path to the local clone root.
        branch_name: Name of the new branch to create.
    """
    await _git(["checkout", "-b", branch_name], cwd=repo_dir)
    logger.info("branch created", extra={"branch": branch_name})


async def write_file(repo_dir: str, relative_path: str, content: str) -> None:
    """
    Write a file to the local clone, creating parent directories as needed.

    Args:
        repo_dir:      Path to the local clone root.
        relative_path: File path relative to the repo root.
        content:       Text content to write.
    """
    full_path = Path(repo_dir) / relative_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    logger.info("file written", extra={"path": relative_path})


async def commit_and_push(
    repo_dir: str,
    branch_name: str,
    message: str,
    author_name: str = "Helix Bot",
    author_email: str = "helix@helix.bot",
) -> None:
    """
    Stage all changes in the local clone, commit them, and push the branch.

    Args:
        repo_dir:     Path to the local clone root.
        branch_name:  Branch to push to (must already be checked out).
        message:      Commit message.
        author_name:  Git author name (defaults to "Helix Bot").
        author_email: Git author email.
    """
    await _git(["config", "user.name", author_name], cwd=repo_dir)
    await _git(["config", "user.email", author_email], cwd=repo_dir)
    await _git(["add", "--all"], cwd=repo_dir)
    await _git(["commit", "--message", message], cwd=repo_dir)
    await _git(["push", "--set-upstream", "origin", branch_name], cwd=repo_dir)
    logger.info("committed and pushed", extra={"branch": branch_name})


# ---------------------------------------------------------------------------
# GitHub REST API helpers (httpx)
# ---------------------------------------------------------------------------

def _api_headers(token: str | None = None) -> dict[str, str]:
    """Return standard headers for GitHub API requests.

    Args:
        token: GitHub token to use. Falls back to GITHUB_TOKEN env var.
    """
    if token:
        resolved = token
    else:
        logger.warning(
            "no installation token provided — falling back to GITHUB_TOKEN env var; "
            "ideally a GitHub App installation token should be used"
        )
        resolved = os.environ.get("GITHUB_TOKEN")
    if not resolved:
        raise EnvironmentError("GITHUB_TOKEN is not set")
    return {
        "Authorization": f"Bearer {resolved}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def create_pull_request(
    repo: str,
    title: str,
    body: str,
    head: str,
    base: str = "main",
    token: str | None = None,
) -> tuple[int, str]:
    """
    Open a pull request on GitHub.

    Args:
        repo:  Repository in "owner/name" format, e.g. "acme/backend".
        title: PR title.
        body:  PR description (supports Markdown).
        head:  Source branch name (the fix branch).
        base:  Target branch name. Defaults to "main".
        token: GitHub token. Falls back to GITHUB_TOKEN env var.

    Returns:
        (pr_number, pr_url) tuple.

    Raises:
        httpx.HTTPStatusError: If the API request fails.
    """
    url = f"{_GITHUB_API}/repos/{repo}/pulls"
    payload = {"title": title, "body": body, "head": head, "base": base}

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=_api_headers(token))
        response.raise_for_status()

    data = response.json()
    pr_number: int = data["number"]
    pr_url: str = data["html_url"]
    logger.info("pull request created", extra={"pr_number": pr_number, "pr_url": pr_url})
    return pr_number, pr_url


async def merge_pull_request(
    repo: str,
    pr_number: int,
    commit_title: str = "",
    merge_method: str = "squash",
    token: str | None = None,
) -> None:
    """
    Merge a pull request via the GitHub API.

    Args:
        repo:         Repository in "owner/name" format.
        pr_number:    Pull request number.
        commit_title: Optional title for the merge commit. Defaults to the PR title.
        merge_method: One of "merge", "squash", or "rebase". Defaults to "squash".
        token:        GitHub token. Falls back to GITHUB_TOKEN env var.

    Raises:
        httpx.HTTPStatusError: If the API request fails (e.g. PR not mergeable).
    """
    url = f"{_GITHUB_API}/repos/{repo}/pulls/{pr_number}/merge"
    payload: dict = {"merge_method": merge_method}
    if commit_title:
        payload["commit_title"] = commit_title

    async with httpx.AsyncClient() as client:
        response = await client.put(url, json=payload, headers=_api_headers(token))
        response.raise_for_status()

    logger.info("pull request merged", extra={"repo": repo, "pr_number": pr_number})


async def find_existing_issue(repo: str, title: str, token: str | None = None) -> tuple[str, str] | None:
    """
    Search for an open GitHub Issue with a matching title in the given repo.

    Used by the QA Agent to deduplicate: if an issue for this bug already
    exists, add a comment rather than opening a duplicate.

    Args:
        repo:  Repository in "owner/name" format, e.g. "acme/backend".
        title: Issue title to search for.
        token: GitHub token. Falls back to GITHUB_TOKEN env var.

    Returns:
        (issue_number_str, issue_url) if a match is found, or None.

    Raises:
        httpx.HTTPStatusError: If the API request fails.
    """
    # Use the search API to find open issues with a matching title.
    query = f'repo:{repo} is:issue is:open in:title {title[:60]}'
    url = f"{_GITHUB_API}/search/issues"

    async with httpx.AsyncClient() as client:
        response = await client.get(
            url,
            params={"q": query, "per_page": 1},
            headers=_api_headers(token),
        )
        response.raise_for_status()

    items = response.json().get("items", [])
    if not items:
        return None

    issue = items[0]
    issue_number = str(issue["number"])
    issue_url = issue["html_url"]
    logger.info("existing github issue found", extra={"issue_number": issue_number})
    return issue_number, issue_url


async def create_issue(
    repo: str,
    title: str,
    body: str,
    labels: list[str] | None = None,
    token: str | None = None,
) -> tuple[str, str]:
    """
    Create a new GitHub Issue.

    Args:
        repo:   Repository in "owner/name" format, e.g. "acme/backend".
        title:  Issue title.
        body:   Issue body (supports Markdown).
        labels: Optional list of label names to apply.
        token:  GitHub token. Falls back to GITHUB_TOKEN env var.

    Returns:
        (issue_number_str, issue_url) tuple.

    Raises:
        httpx.HTTPStatusError: If the API request fails.
    """
    url = f"{_GITHUB_API}/repos/{repo}/issues"
    payload: dict = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=_api_headers(token))
        response.raise_for_status()

    data = response.json()
    issue_number = str(data["number"])
    issue_url = data["html_url"]
    logger.info("github issue created", extra={"issue_number": issue_number, "issue_url": issue_url})
    return issue_number, issue_url


async def add_issue_comment(repo: str, issue_number: str, comment: str, token: str | None = None) -> None:
    """
    Add a comment to an existing GitHub Issue.

    Args:
        repo:         Repository in "owner/name" format.
        issue_number: Issue number as a string, e.g. "42".
        comment:      Comment body (supports Markdown).
        token:        GitHub token. Falls back to GITHUB_TOKEN env var.

    Raises:
        httpx.HTTPStatusError: If the API request fails.
    """
    url = f"{_GITHUB_API}/repos/{repo}/issues/{issue_number}/comments"
    payload = {"body": comment}

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=_api_headers(token))
        response.raise_for_status()

    logger.info("github issue comment added", extra={"issue_number": issue_number})


async def get_pr_diff(repo: str, pr_number: int, token: str | None = None) -> str:
    """
    Fetch the unified diff of a pull request.

    Args:
        repo:      Repository in "owner/name" format.
        pr_number: Pull request number.
        token:     GitHub token. Falls back to GITHUB_TOKEN env var.

    Returns:
        Unified diff string.

    Raises:
        httpx.HTTPStatusError: If the API request fails.
    """
    url = f"{_GITHUB_API}/repos/{repo}/pulls/{pr_number}"
    headers = {**_api_headers(token), "Accept": "application/vnd.github.diff"}

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()

    return response.text
