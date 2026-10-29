"""Tests for the questionnaire seed loader.

Two halves. The spec validation tests are pure and fast: they check that content errors are
caught *before* anything reaches the database, with a message naming the question rather than a
constraint. The seeding tests check the rows that come out the other side.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AnswerType, HollandTrait, Question, QuestionCategory
from app.seed import QuestionnaireSpec, seed_questionnaire

SEEDS_DIR = Path(__file__).resolve().parents[1] / "seeds"

# Every shipped questionnaire, so a new version is validated the moment it's added.
QUESTIONNAIRE_FILES = sorted(SEEDS_DIR.glob("core-instrument-*.yaml"))

# The version `--activate` serves. Fixtures elsewhere point here rather than naming a file, so
# authoring a new version exercises it everywhere instead of leaving tests on stale content.
CURRENT_QUESTIONNAIRE = SEEDS_DIR / "core-instrument-v2.yaml"


def spec_from(questions: list[dict]) -> QuestionnaireSpec:
    return QuestionnaireSpec.model_validate(
        {"nickname": "fixture", "version": "v1", "questions": questions}
    )


# --------------------------------------------------------------------- content validation


def test_a_choice_question_needs_options() -> None:
    with pytest.raises(ValidationError, match="needs options"):
        spec_from([{"category": "interests", "type": "single_choice", "prompt": "Pick one"}])


def test_a_free_text_question_cannot_have_options() -> None:
    with pytest.raises(ValidationError, match="cannot have options"):
        spec_from(
            [
                {
                    "category": "strengths",
                    "type": "free_text",
                    "prompt": "Tell us about it",
                    "options": [{"label": "Nope"}],
                }
            ]
        )


def test_a_scale_question_needs_bounds() -> None:
    with pytest.raises(ValidationError, match="needs scale_min and scale_max"):
        spec_from([{"category": "eq", "type": "scale", "prompt": "I stay calm"}])


def test_only_scale_questions_can_be_reverse_scored() -> None:
    with pytest.raises(ValidationError, match="reverse_scored"):
        spec_from(
            [
                {
                    "category": "strengths",
                    "type": "free_text",
                    "prompt": "Tell us about it",
                    "reverse_scored": True,
                }
            ]
        )


def test_max_selections_cannot_exceed_the_options() -> None:
    """Not a database constraint — it spans a table and its children — but a content bug."""
    with pytest.raises(ValidationError, match="exceeds the option count"):
        spec_from(
            [
                {
                    "category": "interests",
                    "type": "multi_choice",
                    "prompt": "Pick some",
                    "max_selections": 5,
                    "options": [{"label": "One"}, {"label": "Two"}],
                }
            ]
        )


def test_an_unknown_field_is_rejected() -> None:
    """extra='forbid' turns a typo in the YAML into an error instead of a silent default."""
    with pytest.raises(ValidationError):
        spec_from(
            [
                {
                    "category": "eq",
                    "type": "scale",
                    "prompt": "I stay calm",
                    "scale_min": 1,
                    "scale_max": 5,
                    "reverse_scoring": True,
                }
            ]
        )


@pytest.mark.parametrize("path", QUESTIONNAIRE_FILES, ids=lambda path: path.stem)
def test_every_shipped_questionnaire_is_valid(path: Path) -> None:
    """These files are what people actually edit, so parse them all in CI."""
    spec = QuestionnaireSpec.from_file(path)

    assert spec.nickname == "core-instrument"
    assert {q.category for q in spec.questions} == set(QuestionCategory)


def test_the_current_questionnaire_covers_every_trait() -> None:
    """A trait with no questions can never be anyone's top trait, which skews every report."""
    spec = QuestionnaireSpec.from_file(CURRENT_QUESTIONNAIRE)

    covered = {question.trait for question in spec.questions if question.trait is not None}
    covered |= {
        option.trait
        for question in spec.questions
        for option in question.options
        if option.trait is not None
    }
    assert covered == set(HollandTrait)


# --------------------------------------------------------------------- seeding


async def test_seeding_creates_questions_in_file_order(session: AsyncSession) -> None:
    spec = QuestionnaireSpec.from_file(CURRENT_QUESTIONNAIRE)

    questionnaire_id = await seed_questionnaire(session, spec, publish=False)

    positions = (
        await session.scalars(
            select(Question.position)
            .where(Question.questionnaire_id == questionnaire_id)
            .order_by(Question.position)
        )
    ).all()
    assert list(positions) == list(range(1, len(spec.questions) + 1))

    prompts = (
        await session.scalars(
            select(Question.prompt)
            .where(Question.questionnaire_id == questionnaire_id)
            .order_by(Question.position)
        )
    ).all()
    assert list(prompts) == [q.prompt for q in spec.questions]


async def test_seeding_copies_option_content(session: AsyncSession) -> None:
    spec = QuestionnaireSpec.from_file(CURRENT_QUESTIONNAIRE)
    expected = sum(len(q.options) for q in spec.questions)

    questionnaire_id = await seed_questionnaire(session, spec, publish=False)

    count = await session.scalar(
        text("""
            SELECT count(*)
              FROM question_options o
              JOIN questions q ON q.id = o.question_id
             WHERE q.questionnaire_id = :q
        """),
        {"q": questionnaire_id},
    )
    assert count == expected


async def test_seeding_without_publish_leaves_a_draft(session: AsyncSession) -> None:
    spec = QuestionnaireSpec.from_file(CURRENT_QUESTIONNAIRE)

    questionnaire_id = await seed_questionnaire(session, spec, publish=False)

    published_at = await session.scalar(
        text("SELECT published_at FROM questionnaires WHERE id = :id"), {"id": questionnaire_id}
    )
    assert published_at is None


async def test_publishing_happens_after_the_content_is_written(session: AsyncSession) -> None:
    """If publish ran before the inserts, the immutability triggers would reject them."""
    spec = QuestionnaireSpec.from_file(CURRENT_QUESTIONNAIRE)

    questionnaire_id = await seed_questionnaire(session, spec, publish=True)

    row = (
        await session.execute(
            text("""
                SELECT q.published_at, q.published_by, count(qq.id) AS questions
                  FROM questionnaires q
                  JOIN questions qq ON qq.questionnaire_id = q.id
                 WHERE q.id = :id
                 GROUP BY q.published_at, q.published_by
            """),
            {"id": questionnaire_id},
        )
    ).one()

    assert row.published_at is not None
    assert row.published_by is not None
    assert row.questions == len(spec.questions)


async def test_seeding_the_same_version_twice_is_refused(session: AsyncSession) -> None:
    spec = QuestionnaireSpec.from_file(CURRENT_QUESTIONNAIRE)
    await seed_questionnaire(session, spec, publish=True)

    with pytest.raises(ValueError, match="already exists"):
        await seed_questionnaire(session, spec, publish=False)


async def test_seeded_scale_questions_keep_their_bounds(session: AsyncSession) -> None:
    spec = QuestionnaireSpec.from_file(CURRENT_QUESTIONNAIRE)

    questionnaire_id = await seed_questionnaire(session, spec, publish=False)

    reversed_question = await session.scalar(
        select(Question).where(
            Question.questionnaire_id == questionnaire_id,
            Question.reverse_scored.is_(True),
        )
    )
    assert reversed_question is not None
    assert reversed_question.answer_type is AnswerType.SCALE
    assert (reversed_question.scale_min, reversed_question.scale_max) == (1, 5)
