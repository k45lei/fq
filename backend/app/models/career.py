"""Career reference data.

There is no `careers` table: individual careers are model-generated, because enumerating every
career that exists isn't practical. What is curated is the *family* list — the closed vocabulary
the model chooses from and the thing its answer is validated against.
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import ARRAY, CHAR, Boolean, Date, DateTime, Integer, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, HollandTrait, pg_enum


class CareerFamily(Base):
    __tablename__ = "career_families"

    id: Mapped[UUID] = mapped_column(server_default=text("gen_random_uuid()"), primary_key=True)
    slug: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    # Handed to the model as anchors. Not meant to be exhaustive — that's the point.
    example_careers: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    # Supports ranking families against a computed Holland code without involving the model.
    holland_code: Mapped[list[HollandTrait]] = mapped_column(
        ARRAY(pg_enum(HollandTrait, "holland_trait")), server_default=text("'{}'")
    )
    # Retires a family without deleting it — historical reports reference it.
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class CareerGuidance(Base):
    """What to actually do about a career: what to study, what to build, what to learn.

    Keyed by normalized career name like `CareerPay`, and for the same reason — careers are
    model-generated free text, so there is nothing to hold a foreign key to. `region` matters
    more here than anywhere: schooling routes differ by country more than pay does.
    """

    __tablename__ = "career_guidance"

    id: Mapped[UUID] = mapped_column(server_default=text("gen_random_uuid()"), primary_key=True)
    normalized_name: Mapped[str] = mapped_column(Text)
    region: Mapped[str] = mapped_column(Text)

    study: Mapped[str | None] = mapped_column(Text)
    project: Mapped[str | None] = mapped_column(Text)
    topic: Mapped[str | None] = mapped_column(Text)

    source: Mapped[str] = mapped_column(Text)
    as_of: Mapped[date] = mapped_column(Date)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CareerPay(Base):
    """Pay figures, looked up by normalized career name. Never supplied by the model.

    No foreign key to anything: careers are free text, so this is joined by name at render
    time. A lookup miss omits the figure rather than fabricating one.
    """

    __tablename__ = "career_pay"

    id: Mapped[UUID] = mapped_column(server_default=text("gen_random_uuid()"), primary_key=True)
    normalized_name: Mapped[str] = mapped_column(Text)
    region: Mapped[str] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(CHAR(3))

    pay_low: Mapped[int | None] = mapped_column(Integer)
    pay_median: Mapped[int | None] = mapped_column(Integer)
    pay_high: Mapped[int | None] = mapped_column(Integer)

    # 'bls_oews' today, 'model_research' later. Text plus a CHECK rather than an enum, because
    # this column is expected to grow and ALTER TYPE ... ADD VALUE is awkward under Alembic.
    source: Mapped[str] = mapped_column(Text)
    occupation_code: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    as_of: Mapped[date] = mapped_column(Date)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
