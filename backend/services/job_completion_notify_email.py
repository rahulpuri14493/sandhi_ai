"""
Optional SMTP email to the business user when a job completes successfully.

This is separate from MCP SMTP tools (agent-driven). Configure platform SMTP in .env
(e.g. Gmail app password on smtp.gmail.com:587) and set JOB_COMPLETION_EMAIL_ENABLED=true.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Optional

from core.config import settings
from db.database import SessionLocal
from models.user import User

logger = logging.getLogger(__name__)


def _smtp_send_message(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    mail_from: str,
    mail_to: str,
    subject: str,
    body: str,
    use_tls: bool,
    use_ssl: bool,
) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = mail_to
    msg.set_content(body)
    ctx = ssl.create_default_context()
    timeout = 30.0
    if use_ssl:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=timeout) as smtp:
            smtp.login(user, password)
            smtp.send_message(msg)
        return
    with smtplib.SMTP(host, port, timeout=timeout) as smtp:
        smtp.ehlo()
        if use_tls:
            smtp.starttls(context=ctx)
            smtp.ehlo()
        smtp.login(user, password)
        smtp.send_message(msg)


def maybe_notify_job_completed_email(*, job_id: int, business_id: int, title: str) -> None:
    if not bool(getattr(settings, "JOB_COMPLETION_EMAIL_ENABLED", False)):
        return
    host = (getattr(settings, "JOB_COMPLETION_SMTP_HOST", None) or "").strip()
    user = (getattr(settings, "JOB_COMPLETION_SMTP_USER", None) or "").strip()
    password = (getattr(settings, "JOB_COMPLETION_SMTP_PASSWORD", None) or "").strip()
    if not host or not user or not password:
        logger.warning(
            "job_completion_email_skip job_id=%s reason=smtp_not_configured",
            job_id,
        )
        return
    port = int(getattr(settings, "JOB_COMPLETION_SMTP_PORT", 587) or 587)
    mail_from = (getattr(settings, "JOB_COMPLETION_EMAIL_FROM", None) or "").strip() or user
    use_tls = bool(getattr(settings, "JOB_COMPLETION_SMTP_USE_TLS", True))
    use_ssl = bool(getattr(settings, "JOB_COMPLETION_SMTP_USE_SSL", False))
    prefix = (getattr(settings, "JOB_COMPLETION_EMAIL_SUBJECT_PREFIX", None) or "[Sandhi AI]").strip()

    db = SessionLocal()
    try:
        biz = db.query(User).filter(User.id == int(business_id)).first()
        if not biz or not (biz.email or "").strip():
            logger.warning("job_completion_email_skip job_id=%s reason=no_business_email", job_id)
            return
        mail_to = biz.email.strip()
        link_base = (getattr(settings, "JOB_COMPLETION_EMAIL_LINK_BASE_URL", None) or "").strip().rstrip("/")
        link_line = f"\nOpen the job: {link_base}/jobs/{job_id}\n" if link_base else ""
        subject = f"{prefix} Job completed: {title}"
        body = (
            f"Your job has finished successfully.\n\n"
            f"Job ID: {job_id}\n"
            f"Title: {title}\n"
            f"{link_line}\n"
            f"If you did not expect this message, you can ignore it.\n"
        )
        _smtp_send_message(
            host=host,
            port=port,
            user=user,
            password=password,
            mail_from=mail_from,
            mail_to=mail_to,
            subject=subject,
            body=body,
            use_tls=use_tls and not use_ssl,
            use_ssl=use_ssl,
        )
        # Do not log raw recipient (PII). Operators correlate by job_id.
        logger.info("job_completion_email_sent job_id=%s", job_id)
    except Exception as exc:
        # No exc_info: tracebacks / SMTP responses can contain paths or server text (CodeQL-aligned).
        logger.warning(
            "job_completion_email_failed job_id=%s err_type=%s",
            job_id,
            type(exc).__name__,
        )
    finally:
        db.close()
