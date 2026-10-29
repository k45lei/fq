"""Generated reports.

A response is scored more than once — free from interests alone, then full after payment — so
every scoring run appends a row and nothing overwrites.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ReportTier, pg_enum


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[UUID] = mapped_column(server_default=text("gen_random_uuid()"), primary_key=True)
    response_id: Mapped[UUID] = mapped_column(
        ForeignKey("questionnaire_responses.id", ondelete="CASCADE")
    )
    tier: Mapped[ReportTier] = mapped_column(pg_enum(ReportTier, "report_tier"))
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Should fold in the vintage of anything the report draws on besides the model — the
    # career_pay dataset especially — so a difference between two reports is attributable.
    scoring_version: Mapped[str] = mapped_column(Text)
    model_version: Mapped[str | None] = mapped_column(Text)
    # Hash of the normalized Q&A set, not the row ids. Makes the consistency requirement
    # measurable, and at the strong end lets an identical input reuse its existing report.
    input_hash: Mapped[str] = mapped_column(Text)
    # The whole rendered report — read whole for screen, email and PDF alike.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReportCareerFamily(Base):
    """The ranked families, lifted out of `payload` so the family ids are a real foreign key.

    Write this table first and render `payload.families` from it: two copies written
    independently drift, one derived from the other cannot.
    """

    __tablename__ = "report_career_families"

    report_id: Mapped[UUID] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"), primary_key=True
    )
    family_id: Mapped[UUID] = mapped_column(
        ForeignKey("career_families.id", ondelete="RESTRICT"), primary_key=True
    )
    rank: Mapped[int] = mapped_column(Integer)
    fit_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
