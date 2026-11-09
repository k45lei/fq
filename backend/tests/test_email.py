"""Tests for emailing a report.

Nothing here talks to Resend — `_deliver` is replaced, so what's exercised is everything
around it: when a send happens, who it goes to, and that it happens once.

The addressing test is the one that matters. Mailing `users.email` instead of `contact_email`
would silently send results to the wrong address whenever someone signs in with a different
account than they typed at capture, which is the common case rather than an edge one.
"""

from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import email as email_module
from app.config import get_settings
from app.models import HollandTrait, QuestionnaireResponse, Report
from app.questionnaires import activate
from app.reports import generate_report
from app.routes import public
from app.seed import QuestionnaireSpec, seed_questionnaire
from app.seed_careers import CareerFamiliesSpec, seed_career_families
from tests.test_api import valid_answers
from tests.test_ownership import begin
from tests.test_reports import answer_interests
from tests.test_seed import CURRENT_QUESTIONNAIRE, SEEDS_DIR


@pytest.fixture
async def live(session: AsyncSession) -> UUID:
    await seed_career_families(
        session, CareerFamiliesSpec.from_file(SEEDS_DIR / "career-families.yaml")
    )
    spec = QuestionnaireSpec.from_file(CURRENT_QUESTIONNAIRE)
    questionnaire_id = await seed_questionnaire(session, spec, publish=True)
    await activate(session, questionnaire_id)
    return questionnaire_id


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    """Capture what would have been delivered, with a key configured so sending is attempted."""
    monkeypatch.setenv("RESEND_API_KEY", "test-key-never-used")
    get_settings.cache_clear()

    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(email_module, "_deliver", lambda **kwargs: captured.append(kwargs))

    yield captured
    get_settings.cache_clear()


async def finished(session: AsyncSession, questionnaire_id: UUID) -> QuestionnaireResponse:
    return await answer_interests(session, questionnaire_id, HollandTrait.REALISTIC)


# --------------------------------------------------------------------- sending


async def test_a_finished_report_is_emailed(
    session: AsyncSession, live: UUID, sent: list[dict[str, Any]]
) -> None:
    response = await finished(session, live)
    report = await generate_report(session, response.id)
    await session.commit()

    assert await email_module.send_report_email(session, report.id) is True
    assert len(sent) == 1
    assert sent[0]["subject"]
    # Both parts, always — a text alternative materially helps deliverability.
    assert sent[0]["html"] and sent[0]["text"]


async def test_it_goes_to_the_address_that_was_asked_for(
    session: AsyncSession, live: UUID, sent: list[dict[str, Any]]
) -> None:
    """contact_email, never users.email — the two diverge the moment someone signs in."""
    response = await finished(session, live)
    report = await generate_report(session, response.id)
    await session.commit()

    await email_module.send_report_email(session, report.id)

    assert sent[0]["to"] == response.contact_email


async def test_the_report_is_attached(
    session: AsyncSession, live: UUID, sent: list[dict[str, Any]]
) -> None:
    """The attachment is what makes the email useful on a device with no access cookie."""
    response = await finished(session, live)
    report = await generate_report(session, response.id)
    await session.commit()

    await email_module.send_report_email(session, report.id)

    assert sent[0]["pdf"] is not None
    assert sent[0]["pdf"].startswith(b"%PDF")
    assert sent[0]["filename"].endswith(".pdf")


async def test_sending_twice_is_refused(
    session: AsyncSession, live: UUID, sent: list[dict[str, Any]]
) -> None:
    """emailed_at makes it idempotent, so a retry can't double-send."""
    response = await finished(session, live)
    report = await generate_report(session, response.id)
    await session.commit()

    assert await email_module.send_report_email(session, report.id) is True
    assert await email_module.send_report_email(session, report.id) is False
    assert len(sent) == 1


async def test_nothing_is_sent_without_a_key(session: AsyncSession, live: UUID) -> None:
    """Email is optional, like sign-in — a missing key is not an error."""
    response = await finished(session, live)
    report = await generate_report(session, response.id)
    await session.commit()

    assert await email_module.send_report_email(session, report.id) is False

    stored = await session.get(Report, report.id)
    assert stored is not None
    assert stored.emailed_at is None


async def test_a_failed_send_stays_retryable(
    session: AsyncSession,
    live: UUID,
    sent: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """emailed_at is stamped only on success, so a failure leaves the work outstanding."""

    def explode(**_: Any) -> None:
        raise RuntimeError("Resend is down")

    monkeypatch.setattr(email_module, "_deliver", explode)

    response = await finished(session, live)
    report = await generate_report(session, response.id)
    await session.commit()

    # Never raises: a failed send must not take down the background task that called it.
    assert await email_module.send_report_email(session, report.id) is False

    stored = await session.get(Report, report.id)
    assert stored is not None
    assert stored.emailed_at is None


# --------------------------------------------------------------------- through the API


async def test_finishing_the_questionnaire_schedules_exactly_one_email(
    client: AsyncClient, live: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One email per completion, not one per section.

    The task itself is replaced rather than run: it opens a session from the application
    engine, which in a test points at the development database. What this checks is the
    decision the route makes — whether to schedule at all, and how often.
    """
    scheduled: list[UUID] = []
    monkeypatch.setattr(public, "email_report_task", lambda report_id: scheduled.append(report_id))

    started: dict[str, Any] = await begin(client)
    submitted = await client.post(
        f"/api/responses/{started['response_id']}/sections/interests",
        json={"answers": valid_answers(started["questionnaire"])},
    )

    assert submitted.status_code == 200
    assert len(scheduled) == 1
    assert scheduled[0] == UUID(submitted.json()["report_id"])


async def test_an_unfinished_questionnaire_schedules_nothing(
    client: AsyncClient, live: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Submitting a *partial* section schedules nothing.

    The questionnaire has three sections; the free tier is one of them. Nothing is sent
    until every section this visitor may answer is done, because a report built from half
    the answers is a different report from the one the visitor is waiting for.
    """
    scheduled: list[UUID] = []
    monkeypatch.setattr(public, "email_report_task", lambda report_id: scheduled.append(report_id))

    started: dict[str, Any] = await begin(client)
    interests = [q for q in started["questionnaire"]["questions"] if q["category"] == "interests"]

    # A section that isn't finished: one question answered, the rest left out.
    partial = valid_answers({"questions": interests})[:1]
    submitted = await client.post(
        f"/api/responses/{started['response_id']}/sections/interests",
        json={"answers": partial},
    )

    assert submitted.status_code == 422
    assert scheduled == []


async def test_the_free_tier_schedules_one_email_and_the_paid_sections_a_second(
    client: AsyncClient, live: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One email per completion, not one per section.

    The free tier is finished when interests is done, so that sends one. Completing the paid
    sections later finishes the questionnaire, and that sends another. Both matter: the first
    is the free report the visitor is entitled to, the second is the upgraded one.
    """
    scheduled: list[UUID] = []
    monkeypatch.setattr(public, "email_report_task", lambda report_id: scheduled.append(report_id))

    started: dict[str, Any] = await begin(client)

    free = await client.post(
        f"/api/responses/{started['response_id']}/sections/interests",
        json={"answers": valid_answers(started["questionnaire"])},
    )
    assert free.status_code == 200
    assert len(scheduled) == 1
    assert scheduled[0] == UUID(free.json()["report_id"])
