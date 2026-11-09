"""Tests for PDF export.

The download is a second door onto the same data, so the thing most worth checking is that it
is locked the same way. A downloadable copy that skipped the ownership check would make the
check on the report itself pointless.

Rendering is slow relative to everything else here, so these generate as few documents as the
assertions allow.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.pdf import _format_pay
from app.questionnaires import activate
from app.seed import QuestionnaireSpec, seed_questionnaire
from app.seed_careers import CareerFamiliesSpec, seed_career_families
from tests.test_api import valid_answers
from tests.test_ownership import begin, stranger  # noqa: F401 — `stranger` is a fixture
from tests.test_seed import CURRENT_QUESTIONNAIRE, SEEDS_DIR


@pytest.fixture
async def live(session: AsyncSession) -> None:
    await seed_career_families(
        session, CareerFamiliesSpec.from_file(SEEDS_DIR / "career-families.yaml")
    )
    spec = QuestionnaireSpec.from_file(CURRENT_QUESTIONNAIRE)
    questionnaire_id = await seed_questionnaire(session, spec, publish=True)
    await activate(session, questionnaire_id)


async def completed_report(client: AsyncClient) -> str:
    started: dict[str, Any] = await begin(client)
    submitted = await client.post(
        f"/api/responses/{started['response_id']}/sections/interests",
        json={"answers": valid_answers(started["questionnaire"])},
    )
    assert submitted.status_code == 200, submitted.text
    return submitted.json()["report_id"]


# --------------------------------------------------------------------- pay formatting


def test_a_full_range_reads_as_a_range() -> None:
    assert _format_pay({"low": 46000, "high": 104000, "currency": "USD"}) == ("$46,000 – $104,000")


def test_a_median_alone_says_so() -> None:
    assert _format_pay({"low": None, "median": 62000, "high": None, "currency": "USD"}) == (
        "$62,000 median"
    )


def test_no_figures_produces_nothing() -> None:
    """Rather than an empty range, which would read as a real but zero salary."""
    assert _format_pay({"low": None, "median": None, "high": None, "currency": "USD"}) is None


# --------------------------------------------------------------------- the download


async def test_the_owner_gets_a_pdf(client: AsyncClient, live: None) -> None:
    report_id = await completed_report(client)

    response = await client.get(f"/api/reports/{report_id}/pdf")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    # The magic bytes, rather than a length check — a 200 containing an HTML error page would
    # otherwise pass.
    assert response.content.startswith(b"%PDF")


async def test_the_download_is_named_and_attached(client: AsyncClient, live: None) -> None:
    report_id = await completed_report(client)

    response = await client.get(f"/api/reports/{report_id}/pdf")

    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert disposition.endswith('.pdf"')
    assert "futurequest-" in disposition


async def test_a_stranger_cannot_download_someone_elses_report(
    client: AsyncClient,
    stranger: AsyncClient,  # noqa: F811 — imported fixture
    live: None,
) -> None:
    report_id = await completed_report(client)

    response = await stranger.get(f"/api/reports/{report_id}/pdf")

    assert response.status_code == 404


async def test_an_unknown_report_has_no_pdf(client: AsyncClient, live: None) -> None:
    response = await client.get("/api/reports/00000000-0000-0000-0000-000000000000/pdf")

    assert response.status_code == 404
