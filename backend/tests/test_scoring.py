"""Tests for deterministic scoring.

The properties worth pinning are the ones the consistency requirement rests on: identical
answers produce identical output, ties break the same way every run, and a trait nobody was
asked about is absent rather than zero.
"""

from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AnswerType, HollandTrait, QuestionnaireResponse
from app.scoring import holland_code, rank_families, score_traits
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
async def questionnaire_id(session: AsyncSession) -> UUID:
    spec = QuestionnaireSpec.from_file(CURRENT_QUESTIONNAIRE)
    return await seed_questionnaire(session, spec, publish=False)


@pytest.fixture
async def scored_response(session: AsyncSession, questionnaire_id: UUID) -> QuestionnaireResponse:
    """A response to score. Named to avoid shadowing the conftest's `response` fixture."""
    return await start_response(session, questionnaire_id)


@pytest.fixture
async def families(session: AsyncSession) -> None:
    spec = CareerFamiliesSpec.from_file(SEEDS_DIR / "career-families.yaml")
    await seed_career_families(session, spec)


# --------------------------------------------------------------------- trait scoring


async def test_an_unanswered_response_scores_nothing(
    session: AsyncSession, scored_response: QuestionnaireResponse
) -> None:
    assert await score_traits(session, scored_response.id) == {}


async def test_a_selected_option_earns_its_trait(
    session: AsyncSession, questionnaire_id: UUID, scored_response: QuestionnaireResponse
) -> None:
    question = (await questions_of(session, questionnaire_id, AnswerType.MULTI_CHOICE))[0]
    realistic = await option_with_trait(session, question, HollandTrait.REALISTIC)

    await answer_choice(session, scored_response, question, [realistic.id])
    scores = await score_traits(session, scored_response.id)

    assert scores[HollandTrait.REALISTIC] == 1.0
    # The other traits were on offer and not picked, so they score zero — but they are present,
    # because the question did ask about them.
    assert scores[HollandTrait.ARTISTIC] == 0.0


async def test_a_trait_nobody_asked_about_is_absent_not_zero(
    session: AsyncSession, questionnaire_id: UUID, scored_response: QuestionnaireResponse
) -> None:
    """'We didn't ask' and 'you scored nothing' are different claims."""
    scale_questions = await questions_of(session, questionnaire_id, AnswerType.SCALE)
    realistic_question = next(q for q in scale_questions if q.trait == HollandTrait.REALISTIC)

    await answer_scale(session, scored_response, realistic_question, 5)
    scores = await score_traits(session, scored_response.id)

    assert scores == {HollandTrait.REALISTIC: 1.0}
    assert HollandTrait.SOCIAL not in scores


async def test_scale_answers_normalise_across_the_range(
    session: AsyncSession, questionnaire_id: UUID, scored_response: QuestionnaireResponse
) -> None:
    scale_questions = await questions_of(session, questionnaire_id, AnswerType.SCALE)
    question = next(q for q in scale_questions if q.trait == HollandTrait.REALISTIC)

    await answer_scale(session, scored_response, question, 3)  # midpoint of 1..5
    scores = await score_traits(session, scored_response.id)

    assert scores[HollandTrait.REALISTIC] == 0.5


async def test_a_reverse_scored_answer_is_inverted(
    session: AsyncSession, questionnaire_id: UUID, scored_response: QuestionnaireResponse
) -> None:
    """Bottom of the range on an inverted item is the *top* of the trait score.

    Reverse scoring failing silently is the nastiest bug in this layer: nothing errors, the
    report just quietly describes someone as the opposite of what they said.
    """
    scale_questions = await questions_of(session, questionnaire_id, AnswerType.SCALE)
    question = next(q for q in scale_questions if q.reverse_scored and q.trait is not None)
    assert question.trait is not None and question.scale_min is not None

    await answer_scale(session, scored_response, question, question.scale_min)
    scores = await score_traits(session, scored_response.id)

    assert scores[question.trait] == 1.0


async def test_a_forward_scored_answer_is_not_inverted(
    session: AsyncSession, questionnaire_id: UUID, scored_response: QuestionnaireResponse
) -> None:
    """The control for the test above — otherwise it would pass with inversion applied twice."""
    scale_questions = await questions_of(session, questionnaire_id, AnswerType.SCALE)
    question = next(q for q in scale_questions if not q.reverse_scored and q.trait is not None)
    assert question.trait is not None and question.scale_min is not None

    await answer_scale(session, scored_response, question, question.scale_min)
    scores = await score_traits(session, scored_response.id)

    assert scores[question.trait] == 0.0


async def test_scores_come_back_highest_first(
    session: AsyncSession, questionnaire_id: UUID, scored_response: QuestionnaireResponse
) -> None:
    question = (await questions_of(session, questionnaire_id, AnswerType.MULTI_CHOICE))[0]
    social = await option_with_trait(session, question, HollandTrait.SOCIAL)

    await answer_choice(session, scored_response, question, [social.id])
    scores = await score_traits(session, scored_response.id)

    assert next(iter(scores)) is HollandTrait.SOCIAL


async def test_identical_answers_score_identically(
    session: AsyncSession, questionnaire_id: UUID
) -> None:
    """The property the whole consistency requirement rests on."""
    question = (await questions_of(session, questionnaire_id, AnswerType.MULTI_CHOICE))[0]
    artistic = await option_with_trait(session, question, HollandTrait.ARTISTIC)

    first = await start_response(session, questionnaire_id)
    second = await start_response(session, questionnaire_id)
    await answer_choice(session, first, question, [artistic.id])
    await answer_choice(session, second, question, [artistic.id])

    assert await score_traits(session, first.id) == await score_traits(session, second.id)


# --------------------------------------------------------------------- holland code


def test_the_code_takes_the_strongest_traits() -> None:
    scores = {
        HollandTrait.SOCIAL: 0.9,
        HollandTrait.ARTISTIC: 0.7,
        HollandTrait.REALISTIC: 0.5,
        HollandTrait.CONVENTIONAL: 0.1,
    }

    assert holland_code(scores) == [
        HollandTrait.SOCIAL,
        HollandTrait.ARTISTIC,
        HollandTrait.REALISTIC,
    ]


def test_the_code_drops_traits_that_scored_nothing() -> None:
    scores = {HollandTrait.SOCIAL: 0.9, HollandTrait.ARTISTIC: 0.0}

    assert holland_code(scores) == [HollandTrait.SOCIAL]


def test_ties_break_on_declaration_order_not_dictionary_order() -> None:
    """Stable output matters more than the tie-break being meaningful."""
    scores = {HollandTrait.SOCIAL: 0.5, HollandTrait.REALISTIC: 0.5}
    reversed_scores = {HollandTrait.REALISTIC: 0.5, HollandTrait.SOCIAL: 0.5}

    assert holland_code(scores) == holland_code(reversed_scores)
    assert holland_code(scores)[0] is HollandTrait.REALISTIC


# --------------------------------------------------------------------- family ranking


async def test_ranking_favours_families_led_by_a_strong_trait(
    session: AsyncSession, families: None
) -> None:
    scores = {trait: 0.0 for trait in HollandTrait}
    scores[HollandTrait.REALISTIC] = 1.0

    ranked = await rank_families(session, scores, limit=3)

    # skilled-trades and engineering both lead on realistic; both should outrank, say, finance.
    assert ranked[0].fit_score > 0
    assert {"skilled-trades", "engineering"} & {fit.slug for fit in ranked}


async def test_ranking_is_stable_for_identical_scores(
    session: AsyncSession, families: None
) -> None:
    scores = {HollandTrait.SOCIAL: 0.8, HollandTrait.ARTISTIC: 0.6}

    first = await rank_families(session, scores)
    second = await rank_families(session, scores)

    assert [fit.slug for fit in first] == [fit.slug for fit in second]


async def test_ranking_respects_the_limit(session: AsyncSession, families: None) -> None:
    scores = {trait: 0.5 for trait in HollandTrait}

    assert len(await rank_families(session, scores, limit=4)) == 4


async def test_inactive_families_are_never_ranked(session: AsyncSession, families: None) -> None:
    from sqlalchemy import update

    from app.models import CareerFamily

    await session.execute(
        update(CareerFamily).where(CareerFamily.slug == "engineering").values(is_active=False)
    )
    await session.flush()

    scores = {HollandTrait.REALISTIC: 1.0}
    ranked = await rank_families(session, scores, limit=20)

    assert "engineering" not in {fit.slug for fit in ranked}
