"""Emailing a finished report.

Sent once per completion: finishing the free section emails the free report, and completing
the paid sections later emails that one too. `reports.emailed_at` makes it idempotent, and a
null there is also the hook for a retry sweep if one is ever needed.

**Delivery is not durable.** This runs in a FastAPI background task, so a restart mid-send
loses the email — there is no retry and nothing is queued. That's a deliberate trade for now;
`CLAUDE.md` says background work belongs on the Postgres queue, and moving this there is the
upgrade. The important part is that it is *off the request*: blocking a questionnaire
submission on an external API call would make finishing feel broken.

Addressed to `questionnaire_responses.contact_email`, never `users.email`. The owning account
can change — signing in re-points a response — and the address someone asked for shouldn't
move with it.
"""

import asyncio
import base64
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import QuestionnaireResponse, Report
from app.origins import canonical_origin
from app.pdf import PdfUnavailableError, _environment, render_report_pdf

logger = logging.getLogger(__name__)


class EmailNotConfiguredError(RuntimeError):
    """No Resend API key — sending is optional and simply unavailable."""


def _render(payload: dict[str, Any], report_url: str) -> tuple[str, str]:
    """The HTML and plain-text bodies. Both, always — a text part helps deliverability."""
    traits = payload.get("traits", {})
    context = {
        "code_labels": [
            traits.get(trait, {}).get("label", trait) for trait in payload.get("holland_code", [])
        ],
        "traits": traits,
        # Three is a summary; the whole list is what the attachment is for.
        "careers": payload.get("careers", [])[:3],
        "report_url": report_url,
    }

    html = _environment.get_template("report_email.html").render(**context)
    text = _environment.get_template("report_email.txt").render(**context)
    return html, text


def _deliver(
    *, to: str, subject: str, html: str, text: str, pdf: bytes | None, filename: str
) -> None:
    """The Resend call. Synchronous and blocking, so callers run it in a thread.

    Separated out so tests can replace it — exercising the real API would send mail.
    """
    import resend  # noqa: PLC0415 - imported lazily, like WeasyPrint, to keep startup quick

    settings = get_settings()
    if settings.resend_api_key is None:
        raise EmailNotConfiguredError("RESEND_API_KEY is not set.")

    resend.api_key = settings.resend_api_key.get_secret_value()

    message: dict[str, Any] = {
        "from": settings.resend_from_email,
        "to": [to],
        "subject": subject,
        "html": html,
        "text": text,
    }
    if pdf is not None:
        # Base64 rather than raw bytes: the SDK accepts both, and a string survives JSON
        # serialisation without surprises.
        message["attachments"] = [{"filename": filename, "content": base64.b64encode(pdf).decode()}]

    resend.Emails.send(message)


async def send_report_email(session: AsyncSession, report_id: UUID) -> bool:
    """Email a report to the address its response was captured with.

    Takes a session rather than opening one. The caller owns the transaction — which is what
    lets tests run this against their own database instead of whatever the cached application
    engine points at.

    Returns True if an email was sent. Never raises: a failed send must not take down the
    background task that called it, and the report itself is unaffected either way.
    """
    settings = get_settings()

    try:
        row = (
            await session.execute(
                select(Report, QuestionnaireResponse.contact_email)
                .join(
                    QuestionnaireResponse,
                    QuestionnaireResponse.id == Report.response_id,
                )
                .where(Report.id == report_id)
            )
        ).first()

        if row is None:
            logger.warning("report %s vanished before it could be emailed", report_id)
            return False

        report, contact_email = row

        if report.emailed_at is not None:
            return False

        if settings.resend_api_key is None:
            logger.info(
                "RESEND_API_KEY is not set; not emailing report %s to %s",
                report_id,
                contact_email,
            )
            return False

        # The canonical origin rather than a per-request one: by the time this runs the
        # request is long gone, so the link has to be a fixed choice.
        html, text = _render(report.payload, f"{canonical_origin()}/report/{report.id}")

        pdf: bytes | None = None
        try:
            pdf = await render_report_pdf(report.payload, report.generated_at)
        except PdfUnavailableError:
            # Worth sending without it. The summary still carries the result, and losing the
            # attachment is better than losing the email.
            logger.warning("PDF unavailable; emailing report %s without one", report_id)

        await asyncio.to_thread(
            _deliver,
            to=contact_email,
            subject="Your crystalBALL results",
            html=html,
            text=text,
            pdf=pdf,
            filename=f"futurequest-{report.generated_at:%Y-%m-%d}.pdf",
        )

        # Stamped only after a successful send, so a failure leaves it retryable.
        report.emailed_at = datetime.now(UTC)
        await session.commit()
        return True

    except Exception:
        logger.exception("failed to email report %s", report_id)
        return False


async def email_report_task(report_id: UUID) -> None:
    """Background-task entry point. The only thing here that owns a session.

    Separate from `send_report_email` so that the part worth testing doesn't have to reach
    for the application's engine — in a test that engine points at the wrong database and was
    built on a different event loop.
    """
    async with get_sessionmaker()() as session:
        await send_report_email(session, report_id)
