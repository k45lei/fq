"""Tests for per-career guidance.

The contract: guidance is attached to the career it's about, and a career we have nothing to
say about shows nothing rather than a placeholder. The coverage test is the important one —
a career reaching a report with no guidance is the failure this replaced.
"""

from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CareerGuidance, HollandTrait
from app.reports import CAREERS_PER_FAMILY, generate_report, normalize_career_name
from app.seed import QuestionnaireSpec, seed_questionnaire
from app.seed_careers import CareerFamiliesSpec, seed_career_families
from app.seed_guidance import GuidanceFile, seed_career_guidance
from tests.test_reports import answer_interests
from tests.test_seed import CURRENT_QUESTIONNAIRE, SEEDS_DIR

GUIDANCE_FILE = SEEDS_DIR / "career-guidance.yaml"


def guidance_file(**overrides: object) -> GuidanceFile:
    base = {
        "source": "authored",
        "region": "US",
        "as_of": "2026-10-02",
        "careers": [{"name": "Electrician", "study": "An apprenticeship.", "topic": "Ohm's law."}],
    }
    return GuidanceFile.model_validate(base | overrides)


# --------------------------------------------------------------------- validation


def test_an_unknown_source_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown source"):
        guidance_file(source="vibes")


def test_an_entry_with_nothing_to_say_is_rejected() -> None:
    with pytest.raises(ValidationError, match="no guidance"):
        guidance_file(careers=[{"name": "Chef"}])


def test_names_colliding_after_normalisation_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate careers"):
        guidance_file(
            careers=[
                {"name": "Software Engineer", "topic": "Python."},
                {"name": "  software   engineer ", "topic": "Go."},
            ]
        )


def test_the_shipped_guidance_file_is_valid() -> None:
    spec = GuidanceFile.from_file(GUIDANCE_FILE)

    assert spec.source == "authored"
    assert len(spec.careers) == 32


def test_guidance_covers_every_career_a_report_can_show() -> None:
    """A suggested career with no next step is the gap this feature exists to close."""
    families = CareerFamiliesSpec.from_file(SEEDS_DIR / "career-families.yaml")
    reachable = {
        normalize_career_name(name)
        for family in families.families
        for name in family.example_careers[:CAREERS_PER_FAMILY]
    }
    covered = {
        normalize_career_name(entry.name) for entry in GuidanceFile.from_file(GUIDANCE_FILE).careers
    }

    assert reachable - covered == set()


# --------------------------------------------------------------------- seeding


async def test_seeding_creates_a_row_per_career(session: AsyncSession) -> None:
    spec = GuidanceFile.from_file(GUIDANCE_FILE)

    created, updated = await seed_career_guidance(session, spec)

    assert (created, updated) == (len(spec.careers), 0)
    count = await session.scalar(select(func.count()).select_from(CareerGuidance))
    assert count == len(spec.careers)


async def test_reseeding_updates_rather_than_duplicating(session: AsyncSession) -> None:
    spec = GuidanceFile.from_file(GUIDANCE_FILE)
    await seed_career_guidance(session, spec)

    created, updated = await seed_career_guidance(session, spec)

    assert (created, updated) == (0, len(spec.careers))


# --------------------------------------------------------------------- in the report


@pytest.fixture
async def content(session: AsyncSession) -> UUID:
    await seed_career_families(
        session, CareerFamiliesSpec.from_file(SEEDS_DIR / "career-families.yaml")
    )
    spec = QuestionnaireSpec.from_file(CURRENT_QUESTIONNAIRE)
    return await seed_questionnaire(session, spec, publish=False)


async def test_every_suggested_career_carries_guidance(
    session: AsyncSession, content: UUID
) -> None:
    await seed_career_guidance(session, GuidanceFile.from_file(GUIDANCE_FILE))
    response = await answer_interests(session, content, HollandTrait.REALISTIC)

    report = await generate_report(session, response.id)

    careers = report.payload["careers"]
    assert careers
    assert all("guidance" in career for career in careers)
    assert all(career["guidance"] for career in careers)


async def test_guidance_is_absent_when_nothing_is_seeded(
    session: AsyncSession, content: UUID
) -> None:
    """No placeholder, no empty object — the key simply isn't there."""
    response = await answer_interests(session, content, HollandTrait.REALISTIC)

    report = await generate_report(session, response.id)

    assert all("guidance" not in career for career in report.payload["careers"])


async def test_only_the_fields_we_have_are_included(session: AsyncSession, content: UUID) -> None:
    """An entry with no `project` must not render an empty one."""
    await seed_career_guidance(session, GuidanceFile.from_file(GUIDANCE_FILE))
    response = await answer_interests(session, content, HollandTrait.REALISTIC)
    target = (await generate_report(session, response.id)).payload["careers"][0]["name"]

    row = await session.scalar(
        select(CareerGuidance).where(
            CareerGuidance.normalized_name == normalize_career_name(target)
        )
    )
    assert row is not None
    row.project = None
    await session.flush()

    report = await generate_report(session, response.id)

    matching = next(c for c in report.payload["careers"] if c["name"] == target)
    assert "project" not in matching["guidance"]
    assert matching["guidance"]["study"]
