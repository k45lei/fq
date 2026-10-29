"""Tests for report assembly.

Two things matter most here. The report must be *reproducible* — identical answers produce an
identical payload and hash, which is the consistency requirement made concrete. And pay must be
absent when it isn't known, never invented, because a fabricated salary in a report aimed at
teenagers is the worst failure this system has.
"""

from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AnswerType,
    CareerPay,
    HollandTrait,
    QuestionnaireResponse,
    ReportCareerFamily,
    ReportTier,
)
from app.reports import (
    SCORING_VERSION,
    build_transcript,
    compute_input_hash,
    generate_report,
    normalize_career_name,
)
from app.seed import QuestionnaireSpec, seed_questionnaire
from app.seed_careers import CareerFamiliesSpec, seed_career_families
from tests.factories import (
    answer_choice,
    answer_scale,
    option_with_trait,
    questions_of,
    start_response,
)
from tests.test_seed import CURRENT_QUESTIONNAIRE, SEEDS_DIR


@pytest.fixture
async def content(session: AsyncSession) -> UUID:
    """A seeded questionnaire and the family vocabulary. Returns the questionnaire id."""
    await seed_career_families(
        session, CareerFamiliesSpec.from_file(SEEDS_DIR / "career-families.yaml")
    )
    spec = QuestionnaireSpec.from_file(CURRENT_QUESTIONNAIRE)
    return await seed_questionnaire(session, spec, publish=False)


async def answer_interests(
    session: AsyncSession, questionnaire_id: UUID, trait: HollandTrait
) -> QuestionnaireResponse:
    """A response leaning hard towards one trait, so the output is predictable."""
    response = await start_response(session, questionnaire_id)

    multi = (await questions_of(session, questionnaire_id, AnswerType.MULTI_CHOICE))[0]
    option = await option_with_trait(session, multi, trait)
    await answer_choice(session, response, multi, [option.id])

    for question in await questions_of(session, questionnaire_id, AnswerType.SCALE):
        if question.trait is not None:
            await answer_scale(session, response, question, 5 if question.trait is trait else 1)

    return response


# --------------------------------------------------------------------- transcript and hash


async def test_the_transcript_reads_as_question_and_answer(
    session: AsyncSession, content: UUID
) -> None:
    response = await start_response(session, content)
    multi = (await questions_of(session, content, AnswerType.MULTI_CHOICE))[0]
    chosen = await option_with_trait(session, multi, HollandTrait.REALISTIC)
    await answer_choice(session, response, multi, [chosen.id])

    transcript = await build_transcript(session, response.id)

    assert transcript
    assert all(pair.prompt for pair in transcript)
    # The multi-choice answer renders as the option's label, not an id — which is what makes
    # the transcript usable as an LLM prompt later.
    assert any(chosen.label in pair.answer for pair in transcript)
    assert any(multi.prompt == pair.prompt for pair in transcript)


async def test_identical_answers_hash_identically(session: AsyncSession, content: UUID) -> None:
    first = await answer_interests(session, content, HollandTrait.SOCIAL)
    second = await answer_interests(session, content, HollandTrait.SOCIAL)

    first_hash = compute_input_hash(await build_transcript(session, first.id))
    second_hash = compute_input_hash(await build_transcript(session, second.id))

    assert first_hash == second_hash


async def test_different_answers_hash_differently(session: AsyncSession, content: UUID) -> None:
    realistic = await answer_interests(session, content, HollandTrait.REALISTIC)
    social = await answer_interests(session, content, HollandTrait.SOCIAL)

    assert compute_input_hash(await build_transcript(session, realistic.id)) != compute_input_hash(
        await build_transcript(session, social.id)
    )


def test_career_name_normalisation_is_case_and_space_insensitive() -> None:
    assert normalize_career_name("  Software   Engineer ") == normalize_career_name(
        "software engineer"
    )


# --------------------------------------------------------------------- payload


async def test_the_report_records_its_provenance(session: AsyncSession, content: UUID) -> None:
    response = await answer_interests(session, content, HollandTrait.INVESTIGATIVE)

    report = await generate_report(session, response.id)

    assert report.scoring_version == SCORING_VERSION
    assert report.model_version is None  # nothing generated it yet
    assert report.tier is ReportTier.FREE
    assert len(report.input_hash) == 64


async def test_the_payload_has_every_documented_section(
    session: AsyncSession, content: UUID
) -> None:
    response = await answer_interests(session, content, HollandTrait.ARTISTIC)

    report = await generate_report(session, response.id)

    assert set(report.payload) == {
        "holland_code",
        "traits",
        "strengths",
        "families",
        "careers",
        "challenges",
    }


async def test_challenges_land_in_the_range_requirements_ask_for(
    session: AsyncSession, content: UUID
) -> None:
    response = await answer_interests(session, content, HollandTrait.ENTERPRISING)

    report = await generate_report(session, response.id)

    assert 5 <= len(report.payload["challenges"]) <= 7


async def test_strengths_match_the_holland_code(session: AsyncSession, content: UUID) -> None:
    """One strength per trait in the code — the report's three-column row depends on it."""
    response = await answer_interests(session, content, HollandTrait.REALISTIC)

    report = await generate_report(session, response.id)

    assert len(report.payload["strengths"]) == len(report.payload["holland_code"])


async def test_families_are_ranked_from_one(session: AsyncSession, content: UUID) -> None:
    response = await answer_interests(session, content, HollandTrait.REALISTIC)

    report = await generate_report(session, response.id)

    ranks = [family["rank"] for family in report.payload["families"]]
    assert ranks == sorted(ranks)
    assert ranks[0] == 1


async def test_the_payload_reflects_the_answers(session: AsyncSession, content: UUID) -> None:
    response = await answer_interests(session, content, HollandTrait.SOCIAL)

    report = await generate_report(session, response.id)

    assert report.payload["holland_code"][0] == HollandTrait.SOCIAL.value


async def test_identical_answers_produce_identical_payloads(
    session: AsyncSession, content: UUID
) -> None:
    """Consistency, made concrete: no ordering or tie-break may vary between runs."""
    first = await answer_interests(session, content, HollandTrait.CONVENTIONAL)
    second = await answer_interests(session, content, HollandTrait.CONVENTIONAL)

    first_report = await generate_report(session, first.id)
    second_report = await generate_report(session, second.id)

    assert first_report.payload == second_report.payload
    assert first_report.input_hash == second_report.input_hash


# --------------------------------------------------------------------- careers and pay


async def test_careers_come_from_the_ranked_families(session: AsyncSession, content: UUID) -> None:
    response = await answer_interests(session, content, HollandTrait.REALISTIC)

    report = await generate_report(session, response.id)

    family_ids = {family["family_id"] for family in report.payload["families"]}
    assert report.payload["careers"]
    assert all(career["family_id"] in family_ids for career in report.payload["careers"])


async def test_pay_is_omitted_when_it_is_not_known(session: AsyncSession, content: UUID) -> None:
    """career_pay is empty, so every lookup misses — and a miss must omit, never invent."""
    response = await answer_interests(session, content, HollandTrait.REALISTIC)

    report = await generate_report(session, response.id)

    assert all("pay" not in career for career in report.payload["careers"])


async def test_pay_is_included_when_it_is_known(session: AsyncSession, content: UUID) -> None:
    response = await answer_interests(session, content, HollandTrait.REALISTIC)
    first_pass = await generate_report(session, response.id)
    known = first_pass.payload["careers"][0]["name"]

    session.add(
        CareerPay(
            normalized_name=normalize_career_name(known),
            region="US",
            currency="USD",
            pay_low=30000,
            pay_median=45000,
            pay_high=60000,
            source="bls_oews",
            as_of=date(2025, 5, 1),
        )
    )
    await session.flush()

    report = await generate_report(session, response.id)

    matching = next(c for c in report.payload["careers"] if c["name"] == known)
    assert matching["pay"]["median"] == 45000
    assert matching["pay"]["currency"] == "USD"


# --------------------------------------------------------------------- persistence


async def test_ranked_families_are_written_as_rows_too(
    session: AsyncSession, content: UUID
) -> None:
    """Family ids in the payload are unenforced; these rows are the foreign key."""
    response = await answer_interests(session, content, HollandTrait.ARTISTIC)

    report = await generate_report(session, response.id)

    rows = (
        await session.scalars(
            select(ReportCareerFamily)
            .where(ReportCareerFamily.report_id == report.id)
            .order_by(ReportCareerFamily.rank)
        )
    ).all()

    assert [row.rank for row in rows] == [f["rank"] for f in report.payload["families"]]
    assert [str(row.family_id) for row in rows] == [
        f["family_id"] for f in report.payload["families"]
    ]


async def test_generating_twice_appends_rather_than_overwriting(
    session: AsyncSession, content: UUID
) -> None:
    """A response is scored more than once — free, then full after payment."""
    response = await answer_interests(session, content, HollandTrait.SOCIAL)

    await generate_report(session, response.id, tier=ReportTier.FREE)
    await generate_report(session, response.id, tier=ReportTier.FULL)

    from app.models import Report

    count = await session.scalar(
        select(func.count()).select_from(Report).where(Report.response_id == response.id)
    )
    assert count == 2
