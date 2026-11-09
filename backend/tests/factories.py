"""Async helpers for building a response and answering it.

`tests/seed.py` covers the sync path used by the DDL tests. These are the async equivalents,
for tests that exercise application code.

`sign_in_directly` also lives here: minting the session cookie is a test concern, and having
it in one place stops a test reaching past the cookie format to fake an identity.
"""

import base64
import json
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AnswerType,
    HollandTrait,
    Question,
    QuestionnaireResponse,
    QuestionOption,
    ResponseAnswer,
    ResponseAnswerOption,
    User,
)


def sign_in_directly(client: AsyncClient, user_id: UUID) -> None:
    """Mint the identity cookie the OAuth callback would set.

    Going through Google would mean mocking its token endpoint, which mostly tests the mock.
    What matters is what a *valid* session grants, so the session is established directly.

    The cookie is an `itsdangerous` `TimestampSigner` signature over the session payload —
    the same format `starlette.middleware.sessions.SessionMiddleware` reads. Writing a bare
    UUID into it produces a cookie that verifies as *not signed in*, which is a silent
    failure: the request succeeds, the identity is simply absent.
    """
    from itsdangerous import TimestampSigner

    secret = get_settings().session_secret
    assert secret is not None, "the session secret must be set for the test suite"
    data = base64.b64encode(json.dumps({"user_id": str(user_id)}).encode())
    signed = TimestampSigner(secret.get_secret_value()).sign(data)
    client.cookies.set("fq_session", signed.decode())


async def create_user(session: AsyncSession, email: str | None = None) -> User:
    # Unique by default: emails are uniquely indexed on lower(email), so a fixed default would
    # collide the moment a test needs two users.
    user = User(name="Test Student", email=email or f"student-{uuid4().hex[:12]}@example.com")
    session.add(user)
    await session.flush()
    return user


async def start_response(
    session: AsyncSession, questionnaire_id: UUID, user: User | None = None
) -> QuestionnaireResponse:
    if user is None:
        user = await create_user(session)

    response = QuestionnaireResponse(
        user_id=user.id, questionnaire_id=questionnaire_id, contact_email=user.email
    )
    session.add(response)
    await session.flush()
    return response


async def answer_scale(
    session: AsyncSession, response: QuestionnaireResponse, question: Question, value: int
) -> None:
    session.add(
        ResponseAnswer(
            response_id=response.id,
            questionnaire_id=response.questionnaire_id,
            question_id=question.id,
            answer_numeric=value,
        )
    )
    await session.flush()


async def answer_choice(
    session: AsyncSession,
    response: QuestionnaireResponse,
    question: Question,
    option_ids: list[UUID],
) -> None:
    session.add(
        ResponseAnswer(
            response_id=response.id,
            questionnaire_id=response.questionnaire_id,
            question_id=question.id,
        )
    )
    await session.flush()

    for option_id in option_ids:
        session.add(
            ResponseAnswerOption(
                response_id=response.id, question_id=question.id, option_id=option_id
            )
        )
    await session.flush()


async def questions_of(
    session: AsyncSession, questionnaire_id: UUID, answer_type: AnswerType
) -> list[Question]:
    return list(
        (
            await session.scalars(
                select(Question)
                .where(
                    Question.questionnaire_id == questionnaire_id,
                    Question.answer_type == answer_type,
                )
                .order_by(Question.position)
            )
        ).all()
    )


async def option_with_trait(
    session: AsyncSession, question: Question, trait: HollandTrait
) -> QuestionOption:
    option = await session.scalar(
        select(QuestionOption).where(
            QuestionOption.question_id == question.id, QuestionOption.trait == trait
        )
    )
    assert option is not None, f"{question.prompt!r} has no {trait} option"
    return option
