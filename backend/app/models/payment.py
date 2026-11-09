"""Money, and what it bought.

`purchases` is the money event; `entitlements` is the access it granted. Splitting them is what
keeps the pricing model out of the schema — per-attempt and time-window pricing are the same
gate query, issuing a different `kind`.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CHAR, DateTime, ForeignKey, Integer, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, EntitlementKind, pg_enum


class DiscountCode(Base):
    __tablename__ = "discount_codes"

    id: Mapped[UUID] = mapped_column(server_default=text("gen_random_uuid()"), primary_key=True)
    # Uniqueness is on upper(code), enforced by a functional index the migration owns.
    code: Mapped[str] = mapped_column(Text)
    stripe_coupon_id: Mapped[str | None] = mapped_column(Text)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_redemptions: Mapped[int | None] = mapped_column(Integer)


class Purchase(Base):
    __tablename__ = "purchases"

    id: Mapped[UUID] = mapped_column(server_default=text("gen_random_uuid()"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    stripe_checkout_session_id: Mapped[str | None] = mapped_column(Text)
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(Text)
    # Keeps the tier/SKU definition in Stripe rather than duplicating a product catalogue here.
    stripe_price_id: Mapped[str | None] = mapped_column(Text)
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(CHAR(3), server_default=text("'usd'"))
    discount_code_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("discount_codes.id", ondelete="SET NULL")
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Recording a refund does NOT revoke access on its own — the gate reads `entitlements`.
    # Refunding must stamp revoked_at on the rows this purchase issued.
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Entitlement(Base):
    __tablename__ = "entitlements"

    id: Mapped[UUID] = mapped_column(server_default=text("gen_random_uuid()"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    kind: Mapped[EntitlementKind] = mapped_column(pg_enum(EntitlementKind, "entitlement_kind"))

    # Exactly one origin: bought, or granted by staff. A CHECK enforces the exclusivity, which
    # is why purchase_id is nullable — a comped report has no Stripe payment behind it.
    purchase_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("purchases.id", ondelete="RESTRICT")
    )
    granted_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("admin_accounts.id", ondelete="RESTRICT")
    )
    grant_note: Mapped[str | None] = mapped_column(Text)

    # Null covers any response — that's what a time-window entitlement looks like.
    response_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("questionnaire_responses.id", ondelete="RESTRICT")
    )
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
