"""Declarative base and the shared Postgres enum types.

**These models are a partial mirror of the schema, not its definition.** The migration in
`alembic/versions/` is the source of truth. Models carry tables, columns and foreign keys —
enough to query with — and deliberately omit CHECK constraints, triggers and functional
indexes, which SQLAlchemy cannot express and which carry much of this schema's integrity.

`tests/test_model_drift.py` is what keeps the two in agreement.
"""

import enum

from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class QuestionCategory(enum.StrEnum):
    INTERESTS = "interests"
    STRENGTHS = "strengths"
    EQ = "eq"


class AnswerType(enum.StrEnum):
    SINGLE_CHOICE = "single_choice"
    MULTI_CHOICE = "multi_choice"
    SCALE = "scale"
    FREE_TEXT = "free_text"


class HollandTrait(enum.StrEnum):
    REALISTIC = "realistic"
    INVESTIGATIVE = "investigative"
    ARTISTIC = "artistic"
    SOCIAL = "social"
    ENTERPRISING = "enterprising"
    CONVENTIONAL = "conventional"


class ReportTier(enum.StrEnum):
    FREE = "free"
    FULL = "full"


class EntitlementKind(enum.StrEnum):
    SINGLE_REPORT = "single_report"
    WINDOW = "window"
    COACHING_SESSION = "coaching_session"


class AdminPermission(enum.StrEnum):
    AUTHOR_QUESTIONNAIRE = "author_questionnaire"
    PUBLISH_QUESTIONNAIRE = "publish_questionnaire"
    MANAGE_CAREERS = "manage_careers"
    MANAGE_DISCOUNT_CODES = "manage_discount_codes"
    VIEW_USER_DATA = "view_user_data"
    MANAGE_PURCHASES = "manage_purchases"
    MANAGE_ADMINS = "manage_admins"


def pg_enum(python_enum: type[enum.Enum], name: str) -> ENUM:
    """Bind to an enum type the migration already created.

    `create_type=False` stops SQLAlchemy emitting CREATE TYPE; `values_callable` makes it send
    the member *values* ('interests') rather than the member names ('INTERESTS').
    """
    return ENUM(
        python_enum,
        name=name,
        create_type=False,
        values_callable=lambda members: [member.value for member in members],
    )
