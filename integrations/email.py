"""
Email notification integration for Helix.

Supports two backends:
  - SendGrid  — used when SENDGRID_API_KEY is set (HTTP API via httpx)
  - SMTP       — fallback (aiosmtplib + SMTP_HOST / SMTP_USER / SMTP_PASSWORD)

Public API:
    await send_escalation(incident_id, crash_summary, attempts, context)
    await send_pr_merged(incident_id, pr_url, pr_number, approved_by)

Environment variables:
    EMAIL_FROM              Sender address
    EMAIL_TO                Comma-separated recipient addresses
    SENDGRID_API_KEY        SendGrid API key (optional — activates SendGrid path)
    SMTP_HOST               SMTP server hostname
    SMTP_PORT               SMTP port (default 587)
    SMTP_USER               SMTP username
    SMTP_PASSWORD           SMTP password
"""

import logging
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

logger = logging.getLogger(__name__)

_SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send"
_DEFAULT_SMTP_PORT = 587


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve(env_var: str, override) -> str:
    """Return override if provided, else read env_var, else raise EnvironmentError."""
    if override is not None:
        return override
    value = os.environ.get(env_var)
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{env_var}' is not set."
        )
    return value


def _recipients(to: str | None) -> list[str]:
    """Parse comma-separated addresses from override or EMAIL_TO env var."""
    raw = to or os.environ.get("EMAIL_TO", "")
    return [addr.strip() for addr in raw.split(",") if addr.strip()]


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

async def _send_sendgrid(
    api_key: str,
    from_addr: str,
    to_addrs: list[str],
    subject: str,
    body_text: str,
    body_html: str,
) -> None:
    """Send via SendGrid Mail Send API."""
    payload = {
        "personalizations": [{"to": [{"email": a} for a in to_addrs]}],
        "from": {"email": from_addr},
        "subject": subject,
        "content": [
            {"type": "text/plain", "value": body_text},
            {"type": "text/html", "value": body_html},
        ],
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            _SENDGRID_URL,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        response.raise_for_status()
    logger.info("email sent via sendgrid", extra={"to": to_addrs, "subject": subject})


async def _send_smtp(
    from_addr: str,
    to_addrs: list[str],
    subject: str,
    body_text: str,
    body_html: str,
    smtp_host: str | None,
    smtp_port: int | None,
    smtp_user: str | None,
    smtp_password: str | None,
) -> None:
    """Send via SMTP using aiosmtplib."""
    import aiosmtplib

    host = _resolve("SMTP_HOST", smtp_host)
    port = smtp_port or int(os.environ.get("SMTP_PORT", _DEFAULT_SMTP_PORT))
    user = _resolve("SMTP_USER", smtp_user)
    password = _resolve("SMTP_PASSWORD", smtp_password)

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = from_addr
    message["To"] = ", ".join(to_addrs)
    message.attach(MIMEText(body_text, "plain"))
    message.attach(MIMEText(body_html, "html"))

    await aiosmtplib.send(
        message,
        hostname=host,
        port=port,
        username=user,
        password=password,
        start_tls=True,
    )
    logger.info("email sent via smtp", extra={"to": to_addrs, "subject": subject})


async def _deliver(
    from_addr: str,
    to_addrs: list[str],
    subject: str,
    body_text: str,
    body_html: str,
) -> None:
    """Route to SendGrid if SENDGRID_API_KEY is set, otherwise SMTP."""
    api_key = os.environ.get("SENDGRID_API_KEY")
    if api_key:
        await _send_sendgrid(api_key, from_addr, to_addrs, subject, body_text, body_html)
    else:
        await _send_smtp(from_addr, to_addrs, subject, body_text, body_html, None, None, None, None)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def send_escalation(
    incident_id: str,
    crash_summary: str,
    attempts: int,
    context: str,
    from_addr: str | None = None,
    to_addrs: str | None = None,
    sendgrid_api_key: str | None = None,
) -> None:
    """Notify on-call that Helix failed to auto-fix an incident after max attempts."""
    from_email = _resolve("EMAIL_FROM", from_addr)
    to_emails = _recipients(to_addrs)
    subject = f"[Helix] Escalation: incident {incident_id} needs manual review"
    body_text = (
        f"Incident: {incident_id}\n\n"
        f"Summary: {crash_summary}\n\n"
        f"Helix made {attempts} fix attempt(s) without success.\n\n"
        f"Context:\n{context}"
    )
    body_html = (
        f"<h2>Helix Escalation: {incident_id}</h2>"
        f"<p><strong>Summary:</strong> {crash_summary}</p>"
        f"<p>Helix made <strong>{attempts}</strong> fix attempt(s) without success.</p>"
        f"<h3>Context</h3><pre>{context}</pre>"
    )
    api_key = sendgrid_api_key or os.environ.get("SENDGRID_API_KEY")
    if api_key:
        await _send_sendgrid(api_key, from_email, to_emails, subject, body_text, body_html)
    else:
        await _send_smtp(from_email, to_emails, subject, body_text, body_html, None, None, None, None)


async def send_pr_merged(
    incident_id: str,
    pr_url: str,
    pr_number: int,
    approved_by: str,
    from_addr: str | None = None,
    to_addrs: str | None = None,
    sendgrid_api_key: str | None = None,
) -> None:
    """Notify that the Helix fix PR was approved and merged."""
    from_email = _resolve("EMAIL_FROM", from_addr)
    to_emails = _recipients(to_addrs)
    subject = f"[Helix] Fix merged for incident {incident_id}"
    body_text = (
        f"Incident: {incident_id}\n\n"
        f"PR #{pr_number} was approved by {approved_by} and merged.\n"
        f"PR URL: {pr_url}"
    )
    body_html = (
        f"<h2>Helix Fix Merged: {incident_id}</h2>"
        f"<p>PR <a href='{pr_url}'>#{pr_number}</a> was approved by "
        f"<strong>{approved_by}</strong> and merged.</p>"
    )
    api_key = sendgrid_api_key or os.environ.get("SENDGRID_API_KEY")
    if api_key:
        await _send_sendgrid(api_key, from_email, to_emails, subject, body_text, body_html)
    else:
        await _send_smtp(from_email, to_emails, subject, body_text, body_html, None, None, None, None)
