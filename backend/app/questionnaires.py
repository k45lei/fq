"""Questionnaire queries — activation, and fetching what should be served.

Activation and publication are separate decisions: publishing freezes a version's content,
activating exposes it to users. That separation is what lets a version be staged and reviewed
before anyone sees it.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Question,
    QuestionCategory,
    Questionnaire,
    QuestionnaireActivation,
    QuestionOption,
)


class NotPublishedError(Exception):
    """Raised when activation is attempted on a draft."""


async def activate(
    session: AsyncSession, questionnaire_id: UUID, *, actor_id: UUID | None = None
) -> QuestionnaireActivation:
    """Make a published questionnaire the live one. Appends; never overwrites."""
    published_at = await session.scalar(
        select(Questionnaire.published_at).where(Questionnaire.id == questionnaire_id)
    )
    if published_at is None:
        # The composite foreign key would reject this anyway — this is here so the caller
        # gets an explanation rather than an integrity error.
        raise NotPublishedError(
            f"questionnaire {questionnaire_id} is a draft; publish it before activating"
        )

    activation = QuestionnaireActivation(
        questionnaire_id=questionnaire_id,
        questionnaire_published_at=published_at,
        activated_by=actor_id,
    )
    session.add(activation)
    await session.flush()
    return activation


async def active_questionnaire_id(session: AsyncSession) -> UUID | None:
    """The live questionnaire, or None if nothing has been activated yet."""
    return await session.scalar(
        select(QuestionnaireActivation.questionnaire_id).order_by(
            QuestionnaireActivation.activated_at.desc()
        )
    )


async def load_section(
    session: AsyncSession, questionnaire_id: UUID, section: QuestionCategory
) -> list[tuple[Question, list[QuestionOption]]]:
    """One section's questions in presentation order, each with its options."""
    questions = list(
        (
            await session.scalars(
                select(Question)
                .where(
                    Question.questionnaire_id == questionnaire_id,
                    Question.category == section,
                )
                .order_by(Question.position)
            )
        ).all()
    )
    if not questions:
        return []

    options = (
        await session.scalars(
            select(QuestionOption)
            .where(QuestionOption.question_id.in_([question.id for question in questions]))
            .order_by(QuestionOption.position)
        )
    ).all()

    grouped: dict[UUID, list[QuestionOption]] = {}
    for option in options:
        grouped.setdefault(option.question_id, []).append(option)

    return [(question, grouped.get(question.id, [])) for question in questions]
