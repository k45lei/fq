"""Starting a response and submitting a section.

This is where the cardinality rules live — the ones `docs/schema.md` identified as impossible to
express declaratively, because they count rows:

* every question in the section is answered
* `single_choice` gets exactly one option, `multi_choice` at most `max_selections`
* a scale answer falls inside the question's bounds

The same pass also sets the section's completion timestamp, which is deliberate: "every required
question is answered correctly" and "this section is complete" are the same check, and splitting
them between a trigger and the endpoint would be worse than keeping both here.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AnswerType,
    Question,
    QuestionCategory,
    QuestionnaireResponse,
    QuestionOption,
    Report,
    ReportTier,
    ResponseAnswer,
    ResponseAnswerOption,
    User,
)
from app.questionnaires import active_questionnaire_id, load_section
from app.reports import generate_report

COMPLETION_COLUMNS = {
    QuestionCategory.INTERESTS: "interests_completed_at",
    QuestionCategory.STRENGTHS: "strengths_completed_at",
    QuestionCategory.EQ: "eq_completed_at",
}


def has_completed(response: QuestionnaireResponse, sections: list[QuestionCategory]) -> bool:
    """Whether every section *available to this visitor* is done.

    Available, not every section that exists — that's what makes "finished the questionnaire"
    mean the same thing on the free tier as with the paywall off. It also gives the right
    number of emails: one on finishing the free section, and one more if the paid sections
    are completed later.
    """
    return all(getattr(response, COMPLETION_COLUMNS[section]) is not None for section in sections)


class NoActiveQuestionnaireError(Exception):
    """Nothing has been activated, so there is nothing to serve."""


class ResponseNotFoundError(Exception):
    pass


class SubmissionError(Exception):
    """The submitted answers don't match the section's questions."""


@dataclass(frozen=True)
class SubmittedAnswer:
    question_id: UUID
    numeric: int | None = None
    text: str | None = None
    option_ids: tuple[UUID, ...] = ()


async def _find_or_create_user(
    session: AsyncSession, name: str, email: str, school: str | None
) -> User:
    # Matched case-insensitively, because that is how the unique index works. A returning
    # visitor starts a new response against the account they already have.
    existing = await session.scalar(select(User).where(User.email.ilike(email)))
    if existing is not None:
        return existing

    user = User(name=name, email=email, school=school)
    session.add(user)
    await session.flush()
    return user


async def start(
    session: AsyncSession,
    *,
    name: str,
    email: str,
    school: str | None = None,
    user_id: UUID | None = None,
) -> tuple[QuestionnaireResponse, UUID]:
    """Begin a response against the live questionnaire.

    `user_id` is the signed-in account, when there is one, and it **wins over the submitted
    email**. Otherwise someone signed in as one address who typed another would have the
    response attached to a different account — invisible in their history, and unreachable
    once the ownership cookie is gone. Claiming can't rescue it either: that runs at sign-in,
    and they were already signed in.

    The browser doesn't offer an email field to a signed-in visitor, so this is a backstop
    rather than the normal path.
    """
    questionnaire_id = await active_questionnaire_id(session)
    if questionnaire_id is None:
        raise NoActiveQuestionnaireError("no questionnaire is active — seed one with --activate")

    user = await session.get(User, user_id) if user_id is not None else None
    if user is None:
        user = await _find_or_create_user(session, name, email, school)
    elif school and not user.school:
        # Worth keeping if we didn't have it; never worth overwriting what's already there.
        user.school = school
        await session.flush()

    response = QuestionnaireResponse(
        user_id=user.id,
        questionnaire_id=questionnaire_id,
        # Signed in, the browser doesn't offer an email field, so the account's address is
        # what was asked for. Anonymous, it's whatever they typed — kept verbatim, since the
        # point of this column is to survive the account changing underneath it.
        contact_email=user.email if user_id is not None else email,
    )
    session.add(response)
    await session.flush()
    return response, questionnaire_id


def _validate(question: Question, options: list[QuestionOption], answer: SubmittedAnswer) -> None:
    valid_option_ids = {option.id for option in options}

    if question.answer_type is AnswerType.SCALE:
        if answer.numeric is None:
            raise SubmissionError(f"{question.prompt!r} needs a numeric answer")
        assert question.scale_min is not None and question.scale_max is not None
        if not question.scale_min <= answer.numeric <= question.scale_max:
            raise SubmissionError(
                f"{question.prompt!r} takes {question.scale_min}–{question.scale_max}, "
                f"got {answer.numeric}"
            )
        return

    if question.answer_type is AnswerType.FREE_TEXT:
        if not (answer.text or "").strip():
            raise SubmissionError(f"{question.prompt!r} needs a written answer")
        return

    chosen = set(answer.option_ids)
    if not chosen:
        raise SubmissionError(f"{question.prompt!r} needs an option selected")

    unknown = chosen - valid_option_ids
    if unknown:
        # The composite foreign key would also reject this; catching it here turns an
        # integrity error into a message naming the question.
        raise SubmissionError(f"{question.prompt!r} was answered with an unknown option")

    if question.answer_type is AnswerType.SINGLE_CHOICE and len(chosen) != 1:
        raise SubmissionError(f"{question.prompt!r} takes exactly one option")

    limit = question.max_selections
    if (
        question.answer_type is AnswerType.MULTI_CHOICE
        and limit is not None
        and len(chosen) > limit
    ):
        raise SubmissionError(f"{question.prompt!r} takes at most {limit} options")


async def submit_section(
    session: AsyncSession,
    response_id: UUID,
    section: QuestionCategory,
    answers: list[SubmittedAnswer],
    *,
    tier: ReportTier = ReportTier.FREE,
) -> Report:
    """Record a section's answers, mark it complete, and generate the report.

    `tier` is decided by the caller, which is the route — the route is the only place that
    knows whether the gate returned a row. Deciding it here would mean asking the gate a
    second time, and the two answers could disagree.
    """
    response = await session.get(QuestionnaireResponse, response_id)
    if response is None:
        raise ResponseNotFoundError(f"no response {response_id}")

    completion_column = COMPLETION_COLUMNS[section]
    if getattr(response, completion_column) is not None:
        raise SubmissionError(f"the {section.value} section has already been submitted")

    questions = await load_section(session, response.questionnaire_id, section)
    if not questions:
        raise SubmissionError(f"this questionnaire has no {section.value} section")

    by_id = {question.id: (question, options) for question, options in questions}
    submitted = {answer.question_id: answer for answer in answers}

    missing = set(by_id) - set(submitted)
    if missing:
        raise SubmissionError(f"{len(missing)} question(s) in this section were not answered")

    extra = set(submitted) - set(by_id)
    if extra:
        raise SubmissionError("answers were submitted for questions outside this section")

    for question_id, answer in submitted.items():
        question, options = by_id[question_id]
        _validate(question, options, answer)

    for question_id, answer in submitted.items():
        question, _ = by_id[question_id]
        session.add(
            ResponseAnswer(
                response_id=response.id,
                questionnaire_id=response.questionnaire_id,
                question_id=question_id,
                answer_numeric=answer.numeric if question.answer_type is AnswerType.SCALE else None,
                answer_text=(
                    (answer.text or "").strip()
                    if question.answer_type is AnswerType.FREE_TEXT
                    else None
                ),
            )
        )
        for option_id in answer.option_ids:
            session.add(
                ResponseAnswerOption(
                    response_id=response.id, question_id=question_id, option_id=option_id
                )
            )

    setattr(response, completion_column, datetime.now(UTC))
    await session.flush()

    return await generate_report(session, response.id, tier=tier)
