"""Questionnaire versions and the questions they own.

A version owns its questions outright — there is no join table and no sharing between versions.
Once `published_at` is set, triggers freeze all three tables; edits mean a new version via
`fork_questionnaire()`.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AnswerType, Base, HollandTrait, QuestionCategory, pg_enum


class Questionnaire(Base):
    __tablename__ = "questionnaires"

    id: Mapped[UUID] = mapped_column(server_default=text("gen_random_uuid()"), primary_key=True)
    nickname: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(Text)
    # Version-level lineage: which version this draft was copied from. There is deliberately
    # no per-item lineage — see "Versions are self-contained" in docs/schema.md.
    derived_from_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("questionnaires.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("admin_accounts.id", ondelete="RESTRICT")
    )
    # Null means draft. Setting it is one-way and freezes everything below.
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("admin_accounts.id", ondelete="RESTRICT")
    )


class QuestionnaireActivation(Base):
    """Append-only record of which questionnaire is live. Most recent row wins.

    `questionnaire_published_at` is denormalized and NOT NULL purely so the composite foreign
    key can only match a questionnaire that has been published — activating a draft is
    unrepresentable rather than merely discouraged.
    """

    __tablename__ = "questionnaire_activations"

    __table_args__ = (
        ForeignKeyConstraint(
            ["questionnaire_id", "questionnaire_published_at"],
            ["questionnaires.id", "questionnaires.published_at"],
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[UUID] = mapped_column(server_default=text("gen_random_uuid()"), primary_key=True)
    questionnaire_id: Mapped[UUID]
    questionnaire_published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # clock_timestamp() rather than now(), so activations within one transaction still order.
    activated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    activated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("admin_accounts.id", ondelete="RESTRICT")
    )


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[UUID] = mapped_column(server_default=text("gen_random_uuid()"), primary_key=True)
    questionnaire_id: Mapped[UUID] = mapped_column(
        ForeignKey("questionnaires.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer)

    category: Mapped[QuestionCategory] = mapped_column(
        pg_enum(QuestionCategory, "question_category")
    )
    answer_type: Mapped[AnswerType] = mapped_column(pg_enum(AnswerType, "answer_type"))
    prompt: Mapped[str] = mapped_column(Text)
    help_text: Mapped[str | None] = mapped_column(Text)
    # Augmenting, not the scoring backbone — reports are generated from Q&A pairs.
    trait: Mapped[HollandTrait | None] = mapped_column(pg_enum(HollandTrait, "holland_trait"))

    # scale questions only
    scale_min: Mapped[int | None] = mapped_column(SmallInteger)
    scale_max: Mapped[int | None] = mapped_column(SmallInteger)
    reverse_scored: Mapped[bool] = mapped_column(server_default=text("false"))

    # multi_choice only
    max_selections: Mapped[int | None] = mapped_column(SmallInteger)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("admin_accounts.id", ondelete="RESTRICT")
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("admin_accounts.id", ondelete="RESTRICT")
    )


class QuestionOption(Base):
    __tablename__ = "question_options"

    # `answer_type` is denormalized from the parent question and pinned to it by the composite
    # foreign key below. That is what makes options on a scale question unrepresentable, and
    # what blocks changing a question's answer_type while options exist.
    __table_args__ = (
        ForeignKeyConstraint(
            ["question_id", "answer_type"],
            ["questions.id", "questions.answer_type"],
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(server_default=text("gen_random_uuid()"), primary_key=True)
    question_id: Mapped[UUID]
    answer_type: Mapped[AnswerType] = mapped_column(pg_enum(AnswerType, "answer_type"))
    position: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(Text)
    score_value: Mapped[int | None] = mapped_column(SmallInteger)
    trait: Mapped[HollandTrait | None] = mapped_column(pg_enum(HollandTrait, "holland_trait"))
