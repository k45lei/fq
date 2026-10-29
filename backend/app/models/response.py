"""A user's attempt at a questionnaire, and the answers they gave.

Only *submitted* answers reach these tables — in-progress drafts live in the browser's
localStorage. The three completion timestamps record section-level progress, which is the
granularity the server needs to survive the paywall round trip.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class QuestionnaireResponse(Base):
    __tablename__ = "questionnaire_responses"

    id: Mapped[UUID] = mapped_column(server_default=text("gen_random_uuid()"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # Pins which version was answered, so revising a questionnaire never retroactively changes
    # what an old response meant.
    questionnaire_id: Mapped[UUID] = mapped_column(
        ForeignKey("questionnaires.id", ondelete="RESTRICT")
    )

    # Where to send this report, captured at the start. Deliberately not read from
    # `users.email`: the owning account can change — signing in re-points a response at a
    # verified account — and the address the person actually asked for shouldn't move with it.
    contact_email: Mapped[str] = mapped_column(Text)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    interests_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    strengths_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    eq_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResponseAnswer(Base):
    __tablename__ = "response_answers"

    # Two composite foreign keys, together making it impossible to answer a question belonging
    # to a different questionnaire version: one pins questionnaire_id to the response it came
    # from, the other pins the question to that version.
    __table_args__ = (
        ForeignKeyConstraint(
            ["response_id", "questionnaire_id"],
            ["questionnaire_responses.id", "questionnaire_responses.questionnaire_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["questionnaire_id", "question_id"],
            ["questions.questionnaire_id", "questions.id"],
            ondelete="RESTRICT",
        ),
    )

    response_id: Mapped[UUID] = mapped_column(primary_key=True)
    question_id: Mapped[UUID] = mapped_column(primary_key=True)
    questionnaire_id: Mapped[UUID]
    # scale answers land here...
    answer_numeric: Mapped[int | None] = mapped_column(SmallInteger)
    # ...free_text here. Choice selections are child rows instead.
    answer_text: Mapped[str | None] = mapped_column(Text)
    answered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ResponseAnswerOption(Base):
    __tablename__ = "response_answer_options"

    # The second constraint is what stops an option from one question being submitted as the
    # answer to another.
    __table_args__ = (
        ForeignKeyConstraint(
            ["response_id", "question_id"],
            ["response_answers.response_id", "response_answers.question_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["question_id", "option_id"],
            ["question_options.question_id", "question_options.id"],
            ondelete="RESTRICT",
        ),
    )

    response_id: Mapped[UUID] = mapped_column(primary_key=True)
    question_id: Mapped[UUID] = mapped_column(primary_key=True)
    option_id: Mapped[UUID] = mapped_column(primary_key=True)
