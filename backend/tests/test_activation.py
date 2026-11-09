"""Tests for questionnaire activation.

Activation is deployment state, kept off the immutable `questionnaires` row. The interesting
property is that activating a draft is *unrepresentable* — the composite foreign key requires a
non-null `published_at`, so it fails at the database even if the application check is bypassed.
"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.questionnaires import NotPublishedError, activate, active_questionnaire_id
from app.seed import QuestionnaireSpec, seed_questionnaire
from tests.test_seed import CURRENT_QUESTIONNAIRE


async def seed(session: AsyncSession, version: str, *, publish: bool) -> UUID:
    spec = QuestionnaireSpec.from_file(CURRENT_QUESTIONNAIRE)
    spec = spec.model_copy(update={"version": version})
    return await seed_questionnaire(session, spec, publish=publish)


async def test_nothing_is_active_initially(session: AsyncSession) -> None:
    assert await active_questionnaire_id(session) is None


async def test_activating_a_published_questionnaire_makes_it_live(
    session: AsyncSession,
) -> None:
    questionnaire_id = await seed(session, "v1", publish=True)

    await activate(session, questionnaire_id)

    assert await active_questionnaire_id(session) == questionnaire_id


async def test_a_draft_cannot_be_activated(session: AsyncSession) -> None:
    questionnaire_id = await seed(session, "v1", publish=False)

    with pytest.raises(NotPublishedError, match="publish it before activating"):
        await activate(session, questionnaire_id)


async def test_a_draft_cannot_be_activated_even_bypassing_the_check(
    session: AsyncSession,
) -> None:
    """The application check is a courtesy; the composite FK is the actual guarantee."""
    questionnaire_id = await seed(session, "v1", publish=False)

    with pytest.raises(IntegrityError, match="violates foreign key constraint"):
        await session.execute(
            text("""
                INSERT INTO questionnaire_activations
                    (questionnaire_id, questionnaire_published_at)
                VALUES (:id, now())
            """),
            {"id": questionnaire_id},
        )


async def test_the_most_recent_activation_wins(session: AsyncSession) -> None:
    first = await seed(session, "v1", publish=True)
    second = await seed(session, "v2", publish=True)

    await activate(session, first)
    await activate(session, second)

    assert await active_questionnaire_id(session) == second


async def test_activations_in_one_transaction_still_order(session: AsyncSession) -> None:
    """Why activated_at defaults to clock_timestamp() rather than now().

    now() is fixed for the whole transaction, so these two rows would tie and "most recent"
    would come down to whatever order the planner returned.
    """
    first = await seed(session, "v1", publish=True)
    second = await seed(session, "v2", publish=True)

    earlier = await activate(session, first)
    later = await activate(session, second)

    assert later.activated_at > earlier.activated_at


async def test_rolling_back_is_another_activation(session: AsyncSession) -> None:
    """Append-only: reverting to a previous version adds a row rather than editing one."""
    first = await seed(session, "v1", publish=True)
    second = await seed(session, "v2", publish=True)

    await activate(session, first)
    await activate(session, second)
    await activate(session, first)

    assert await active_questionnaire_id(session) == first

    history = await session.scalar(
        text("SELECT count(*) FROM questionnaire_activations WHERE questionnaire_id = :id"),
        {"id": first},
    )
    assert history == 2


async def test_activating_an_unknown_questionnaire_is_refused(session: AsyncSession) -> None:
    with pytest.raises(NotPublishedError):
        await activate(session, uuid4())


async def test_the_seed_command_can_activate(session: AsyncSession) -> None:
    spec = QuestionnaireSpec.from_file(CURRENT_QUESTIONNAIRE)

    questionnaire_id = await seed_questionnaire(session, spec, publish=True, activate=True)

    assert await active_questionnaire_id(session) == questionnaire_id
