"""
Crash Handler Agent — FastAPI entry point.

Exposes webhook endpoints for Sentry and Rollbar, plus the Slack actions
endpoint for PR approval:

  POST /webhook/sentry    — Sentry issue-alert webhook
  POST /webhook/rollbar   — Rollbar item-alert webhook
  POST /slack/actions     — Slack button interactions (Approve / Reject PR)
  GET  /healthz           — liveness probe

Run with:
    uvicorn agents.crash_handler.main:app --host 0.0.0.0 --port 8000
"""

import json
import logging
import os
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse

from agents.crash_handler.agent import handle
from core.config import get_github_config, get_redis_url, get_rollbar_config, get_sentry_config, get_slack_config
from core.state import read_pr_result, write_status
from integrations import rollbar as rollbar_integration
from integrations import sentry as sentry_integration
from integrations import slack as slack_integration
from integrations.github import merge_pull_request

_LANDING_PAGE = Path(__file__).parent.parent.parent / "index.html"

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create the Redis client on startup and close it on shutdown."""
    redis_url = get_redis_url()
    logger.info("=== Crash Handler starting ===")
    app.state.redis = aioredis.from_url(redis_url, decode_responses=False)
    logger.info("crash handler started")
    yield
    await app.state.redis.aclose()
    logger.info("crash handler shut down")


app = FastAPI(
    title="Helix — Crash Handler",
    description="Receives Sentry / Rollbar webhooks and drives the incident response pipeline.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/healthz")
async def healthz():
    """Liveness probe — always returns 200 if the server is running."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Sentry webhook
# ---------------------------------------------------------------------------

@app.post("/webhook/sentry", status_code=status.HTTP_202_ACCEPTED)
async def sentry_webhook(request: Request):
    """
    Receive a Sentry issue-alert webhook.

    Verifies the HMAC-SHA256 signature (if SENTRY_WEBHOOK_SECRET is set),
    parses the event, and kicks off the incident pipeline.
    Returns 202 immediately — processing is async.
    """
    body = await request.body()
    signature = request.headers.get("sentry-hook-signature", "")

    logger.info("sentry webhook received", extra={"signature": signature[:20] if signature else "none"})

    sentry_config = get_sentry_config()
    if sentry_config.webhook_secret:
        if not sentry_integration.verify_signature(body, signature, sentry_config.webhook_secret):
            logger.warning("sentry signature mismatch")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature")
    else:
        logger.warning("SENTRY_WEBHOOK_SECRET not set — skipping signature check")

    try:
        raw = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid JSON: {exc}")

    if raw.get("action") == "ping" or raw.get("type") == "ping":
        logger.info("sentry ping received — acknowledged")
        return {"status": "ok"}

    crash_event = sentry_integration.parse_event(raw)
    report = await handle(crash_event, request.app.state.redis)
    logger.info("sentry webhook accepted", extra={"incident_id": report.incident_id})
    return {"incident_id": report.incident_id, "status": "accepted"}


# ---------------------------------------------------------------------------
# Rollbar webhook
# ---------------------------------------------------------------------------

@app.post("/webhook/rollbar", status_code=status.HTTP_202_ACCEPTED)
async def rollbar_webhook(request: Request):
    """
    Receive a Rollbar item-alert webhook.

    Verifies the access token embedded in the payload, parses the event,
    and kicks off the incident pipeline.
    Returns 202 immediately — processing is async.
    """
    body = await request.body()

    try:
        raw = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid JSON: {exc}")

    if raw.get("event_name") == "test":
        logger.info("rollbar connectivity test received — acknowledged")
        return {"status": "ok"}

    rollbar_config = get_rollbar_config()
    if not rollbar_integration.verify_token(raw, rollbar_config.access_token):
        logger.warning("rollbar token mismatch")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")

    crash_event = rollbar_integration.parse_event(raw)
    report = await handle(crash_event, request.app.state.redis)
    logger.info("rollbar webhook accepted", extra={"incident_id": report.incident_id})
    return {"incident_id": report.incident_id, "status": "accepted"}


# ---------------------------------------------------------------------------
# Slack actions (PR approval)
# ---------------------------------------------------------------------------

@app.post("/slack/actions", status_code=status.HTTP_200_OK)
async def slack_actions(request: Request):
    """
    Receive a Slack interactive component payload (button click).

    Handles Approve / Reject button clicks from the PR approval message
    posted by the Notifier Agent.

    On Approve: merges the PR via GitHub API and sets status to pr_merged.
    On Reject:  sets status to approval_rejected.

    Requires SLACK_SIGNING_SECRET — returns 403 if absent or invalid.
    """
    body = await request.body()

    slack_config = get_slack_config()
    if not slack_config.signing_secret:
        logger.warning("slack actions called but SLACK_SIGNING_SECRET is not configured")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Slack signing secret not configured",
        )

    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not slack_integration.verify_signature(body, timestamp, signature, slack_config.signing_secret):
        logger.warning("slack actions signature verification failed")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Slack signature",
        )

    try:
        form = urllib.parse.parse_qs(body.decode("utf-8"))
        payload = json.loads(form["payload"][0])
    except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not parse Slack payload: {exc}",
        )

    actions = payload.get("actions", [])
    if not actions:
        return {"text": "No action found in payload."}

    action = actions[0]
    action_id = action.get("action_id", "")
    incident_id = action.get("value", "")

    logger.info("slack action received", extra={"action_id": action_id, "incident_id": incident_id})

    if action_id == "approve_pr":
        pr_result = await read_pr_result(request.app.state.redis, incident_id)
        if pr_result is None:
            return {"text": f"Could not find PR for incident `{incident_id}`. It may have expired."}

        try:
            github_config = get_github_config()
            await merge_pull_request(
                repo=github_config.target_repo,
                pr_number=pr_result.pr_number,
            )
        except Exception as exc:
            logger.error("pr merge failed", extra={"incident_id": incident_id, "error": str(exc)}, exc_info=True)
            return {"text": f":x: Merge failed for PR #{pr_result.pr_number}. Check logs for details."}

        await write_status(request.app.state.redis, incident_id, "pr_merged")
        logger.info("pr approved and merged", extra={"incident_id": incident_id, "pr_number": pr_result.pr_number})
        return {"text": f":white_check_mark: PR #{pr_result.pr_number} merged. Incident `{incident_id}` resolved."}

    if action_id == "reject_pr":
        await write_status(request.app.state.redis, incident_id, "approval_rejected")
        logger.info("pr rejected", extra={"incident_id": incident_id})
        return {"text": f":x: PR rejected for incident `{incident_id}`. The branch remains open for manual review."}

    logger.warning("unknown slack action_id", extra={"action_id": action_id})
    return {"text": f"Unknown action: {action_id}"}


# ---------------------------------------------------------------------------
# Landing page
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def serve_landing():
    """Serve the static landing page at the root URL."""
    if _LANDING_PAGE.exists():
        return FileResponse(str(_LANDING_PAGE))
    return {"message": "Helix is running. Configure your Sentry/Rollbar webhook to POST /webhook/sentry or /webhook/rollbar."}


@app.get("/favicon.svg", include_in_schema=False)
async def serve_favicon():
    """Serve the SVG favicon."""
    favicon = _LANDING_PAGE.parent / "favicon.svg"
    if favicon.exists():
        return FileResponse(str(favicon), media_type="image/svg+xml")
    raise HTTPException(status_code=404, detail="favicon not found")
