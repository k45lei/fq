"""Tests for the career pay loader.

The contract these protect: a figure in a report is either sourced or clearly labelled as not,
and there is no third state. `source` is the only thing carrying that distinction, so it is
worth checking that it survives to the payload and that a real figure outranks a placeholder.
"""

from datetime import date
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CareerPay, HollandTrait
from app.reports import generate_report, normalize_career_name
from app.seed import QuestionnaireSpec, seed_questionnaire
from app.seed_careers import CareerFamiliesSpec, seed_career_families
from app.seed_pay import CareerPayFile, seed_career_pay
from tests.test_reports import answer_interests
from tests.test_seed import CURRENT_QUESTIONNAIRE, SEEDS_DIR

PAY_FILE = SEEDS_DIR / "career-pay-mock.yaml"


def pay_file(**overrides: object) -> CareerPayFile:
    base = {
        "source": "mock",
        "region": "US",
        "currency": "USD",
        "as_of": "2026-09-21",
        "careers": [{"name": "Electrician", "low": 46000, "median": 62000, "high": 104000}],
    }
    return CareerPayFile.model_validate(base | overrides)


# --------------------------------------------------------------------- validation


def test_an_unknown_source_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown source"):
        pay_file(source="vibes")


def test_figures_must_be_in_order() -> None:
    with pytest.raises(ValidationError, match="out of order"):
        pay_file(careers=[{"name": "Chef", "low": 90000, "median": 50000}])


def test_an_entry_with_no_figures_is_rejected() -> None:
    with pytest.raises(ValidationError, match="no figures"):
        pay_file(careers=[{"name": "Chef"}])


def test_names_colliding_after_normalisation_are_rejected() -> None:
    """They'd overwrite each other on the unique key — better to fail on the file."""
    with pytest.raises(ValidationError, match="duplicate careers"):
        pay_file(
            careers=[
                {"name": "Software Engineer", "median": 100000},
                {"name": "  software   engineer ", "median": 120000},
            ]
        )


def test_the_shipped_mock_file_is_valid() -> None:
    spec = CareerPayFile.from_file(PAY_FILE)

    assert spec.source == "mock"
    assert len(spec.careers) == 32


def test_the_mock_file_covers_every_career_a_report_can_show() -> None:
    """Only the first two example_careers of each family can appear (CAREERS_PER_FAMILY = 2)."""
    from app.reports import CAREERS_PER_FAMILY

    families = CareerFamiliesSpec.from_file(SEEDS_DIR / "career-families.yaml")
    reachable = {
        normalize_career_name(name)
        for family in families.families
        for name in family.example_careers[:CAREERS_PER_FAMILY]
    }
    priced = {
        normalize_career_name(entry.name) for entry in CareerPayFile.from_file(PAY_FILE).careers
    }

    assert reachable - priced == set()


# --------------------------------------------------------------------- seeding


async def test_seeding_creates_a_row_per_career(session: AsyncSession) -> None:
    spec = CareerPayFile.from_file(PAY_FILE)

    created, updated = await seed_career_pay(session, spec)

    assert (created, updated) == (len(spec.careers), 0)
    count = await session.scalar(select(func.count()).select_from(CareerPay))
    assert count == len(spec.careers)


async def test_reseeding_updates_rather_than_duplicating(session: AsyncSession) -> None:
    spec = CareerPayFile.from_file(PAY_FILE)
    await seed_career_pay(session, spec)

    created, updated = await seed_career_pay(session, spec)

    assert (created, updated) == (0, len(spec.careers))


async def test_names_are_stored_normalised(session: AsyncSession) -> None:
    await seed_career_pay(session, pay_file())

    stored = await session.scalar(select(CareerPay.normalized_name))
    assert stored == normalize_career_name("Electrician")


# --------------------------------------------------------------------- in the report


@pytest.fixture
async def content(session: AsyncSession) -> UUID:
    await seed_career_families(
        session, CareerFamiliesSpec.from_file(SEEDS_DIR / "career-families.yaml")
    )
    spec = QuestionnaireSpec.from_file(CURRENT_QUESTIONNAIRE)
    return await seed_questionnaire(session, spec, publish=False)


async def test_mock_figures_reach_the_report_labelled_as_mock(
    session: AsyncSession, content: UUID
) -> None:
    await seed_career_pay(session, CareerPayFile.from_file(PAY_FILE))
    response = await answer_interests(session, content, HollandTrait.REALISTIC)

    report = await generate_report(session, response.id)

    priced = [career for career in report.payload["careers"] if "pay" in career]
    assert priced, "the mock file should cover every career a report can show"
    assert all(career["pay"]["source"] == "mock" for career in priced)


async def test_a_real_figure_outranks_a_placeholder(session: AsyncSession, content: UUID) -> None:
    await seed_career_pay(session, CareerPayFile.from_file(PAY_FILE))
    response = await answer_interests(session, content, HollandTrait.REALISTIC)

    # Whichever career leads this profile, give it a sourced figure alongside the mock one.
    first_pass = await generate_report(session, response.id)
    target = first_pass.payload["careers"][0]["name"]

    session.add(
        CareerPay(
            normalized_name=normalize_career_name(target),
            region="US",
            currency="USD",
            pay_median=123456,
            source="bls_oews",
            source_url="https://www.bls.gov/oes/",
            as_of=date(2025, 5, 1),
        )
    )
    await session.flush()

    report = await generate_report(session, response.id)

    matching = next(c for c in report.payload["careers"] if c["name"] == target)
    assert matching["pay"]["source"] == "bls_oews"
    assert matching["pay"]["median"] == 123456
