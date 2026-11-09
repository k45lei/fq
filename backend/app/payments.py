"""Turning a completed Stripe checkout into a purchase and an entitlement.

One function — `grant_purchase` — does the writing, and it is called from two places: the
webhook, and the return-path check that the frontend calls after Stripe sends the browser
back. **They share the implementation on purpose.** The webhook may be late, may be delayed,
may have been lost; a user who has just paid and is redirected straight back must not be
told they have no access because a background delivery hasn't landed yet. If there are two
implementations of "a completed checkout becomes access", they will disagree, and the
disagreement will be the case where a paying user is refused.

**Idempotency comes from the schema, not from a pre-check.** `purchases` has UNIQUE
constraints on `stripe_checkout_session_id` and `stripe_payment_intent_id`, so
`INSERT ... ON CONFLICT DO NOTHING` makes a redelivered event harmless. The obvious
alternative — read the row, and if it exists, return early — is a race: two deliveries of the
same event can both read "not there" and both write. The unique index is the arbiter.

**The purchase records what Stripe charged, not what we meant to charge.** `amount_total` is
already net of any discount, and is in the currency Stripe settled in. A coupon, or a
currency change, or a tax-inclusive total, would all make our configured figure wrong, and
`purchases.amount_cents` is the record of what was paid.
"""

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import stripe
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import stripe_client
from app.models import Entitlement, Purchase, QuestionnaireResponse

logger = logging.getLogger(__name__)

# The Stripe event types this module acts on. Everything else is acknowledged and ignored —
# see `docs/plan.md` for why ignoring is the right answer rather than an error.
CHECKOUT_COMPLETED = "checkout.session.completed"
CHARGE_REFUNDED = "charge.refunded"

# Delayed payment methods — bank debits, vouchers, `paypal` in some configurations — don't
# settle when the session completes. Stripe fires `checkout.session.completed` with
# `payment_status` of `unpaid`, and then a *second* event when the money arrives. Without
# this, a customer who pays by bank transfer would pay and receive nothing.
ASYNC_PAYMENT_SUCCEEDED = "checkout.session.async_payment_succeeded"
ASYNC_PAYMENT_FAILED = "checkout.session.async_payment_failed"


class PaymentNotReadyError(RuntimeError):
    """The checkout session isn't (yet) something we can grant access for.

    Either the money hasn't arrived — `payment_status` is not `paid`, which happens for
    delayed payment methods — or the session is missing the metadata we need to know what
    was bought. Both are "not now", not "never", and both are logged rather than raised to
    the client.
    """


class MalformedEventError(RuntimeError):
    """The body is signed correctly, but it isn't a Stripe event.

    A different thing from "an event we don't handle". An event of an unknown *type* is
    well-formed and is acknowledged and ignored; a body with no `type` at all isn't an
    event, and there is nothing to acknowledge.

    The signature verified, so the body really did come from Stripe — which means this
    should never happen. It is refused rather than crashed on, because a 500 would make
    Stripe retry the same unusable body for three days.
    """


def parse_uuid(value: str | None) -> UUID | None:
    """A UUID from the metadata, or None if it isn't one.

    The metadata is whatever was in the Checkout Session, which is whatever we put there
    when the session was created — but a session can also have been created by hand in the
    Stripe dashboard, and then the metadata is empty. A malformed id must not be an
    exception; it must be a refusal.
    """
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def stripe_field[T](obj: Any, key: str, default: T | None = None) -> T | None:
    """One attribute of a Stripe object, or None if it isn't there.

    **A missing key is an `AttributeError`, not a `None`.** The SDK's `StripeObject` is a
    `dict` subclass that raises `AttributeError` for a key it doesn't carry — so
    `charge.payment_intent` on a charge with no payment intent *raises* rather than
    returning `None`, and `event.type` on a body that isn't an event raises too.

    That matters because Stripe's objects are whatever was sent. A session created by hand
    in the dashboard has no `metadata`; a charge refunded without a payment intent has no
    `payment_intent`; a body that isn't an event has no `type`. Each of those used to
    crash the webhook with a 500, which Stripe then retries for three days.

    So every read of a Stripe field goes through here, and a missing field is a refusal
    rather than an exception.
    """
    try:
        value = getattr(obj, key)
    except AttributeError:
        return default
    return value if value is not None else default


def metadata_value(checkout: stripe.checkout.Session, key: str) -> str | None:
    """One value from a Checkout Session's metadata.

    **The metadata is not a dict.** The SDK converts a nested JSON object into a
    `StripeObject`, which supports `metadata[key]` but raises `AttributeError` on
    `metadata.get(...)` — the same trap as calling `.get()` on the session itself, one level
    down. It can also be absent entirely, on a session created by hand in the Stripe
    dashboard: the SDK raises `AttributeError` for a *missing* key, and only for a key
    present with a null value does it hand back `None`. So the attribute read is guarded as
    well as the index, or a hand-made session would crash the webhook.

    Then the read is written defensively: index it, catch the miss, and insist the value is
    a string, because metadata values are always strings on the way in and anything else means
    the session wasn't created by us.
    """
    metadata = stripe_field(checkout, "metadata")
    if metadata is None:
        return None
    try:
        value = metadata[key]
    except (KeyError, TypeError):
        return None
    return value if isinstance(value, str) else None


async def grant_purchase(
    session: AsyncSession,
    checkout: stripe.checkout.Session,
    *,
    paid_at: datetime | None = None,
) -> Purchase:
    """Record a completed checkout: one purchase, one entitlement.

    Idempotent. Calling it twice with the same session leaves one purchase and one
    entitlement, because the insert is `ON CONFLICT DO NOTHING` against the unique
    constraint on `stripe_checkout_session_id`.

    The entitlement goes to **the response's owner**, read from the response row, not to
    whatever `user_id` the metadata carried. The gate looks entitlements up by user, so an
    entitlement attached to the wrong user is an entitlement nobody can use — and one
    attached to a user who doesn't own the response is an access-control hole. The metadata's
    copy is a cross-check: if it disagrees, something has gone wrong and the event is logged
    and refused rather than guessed at.

    `paid_at` is when the money was observed to have arrived, which the caller knows better
    than this function does: the webhook has the event's own timestamp, and the return path
    only has the moment it looked. It defaults to now, which is what the return path wants.
    """
    if stripe_field(checkout, "payment_status") != "paid":
        raise PaymentNotReadyError(
            f"checkout session {checkout.id} is not paid (payment_status="
            f"{stripe_field(checkout, 'payment_status')!r}); no access granted"
        )

    response_id = parse_uuid(metadata_value(checkout, "response_id"))
    metadata_user_id = parse_uuid(metadata_value(checkout, "user_id"))
    if response_id is None:
        raise PaymentNotReadyError(
            f"checkout session {checkout.id} names no usable response; no access granted"
        )

    response = await session.get(QuestionnaireResponse, response_id)
    if response is None:
        raise PaymentNotReadyError(
            f"checkout session {checkout.id} names response {response_id}, which no longer "
            f"exists; no access granted"
        )

    if metadata_user_id is not None and metadata_user_id != response.user_id:
        # The response changed hands between the checkout and the delivery — a visitor signed
        # in, and `app/auth.py:claim_responses` re-pointed the response at a verified
        # account. The entitlement follows the response, so the person who ends up with the
        # report is the one who can use it. Logged, because it should not happen often.
        logger.warning(
            "checkout session %s was started by user %s but response %s now belongs to %s; "
            "granting to the response's owner",
            checkout.id,
            metadata_user_id,
            response_id,
            response.user_id,
        )

    # When the money arrived. **Not** `checkout.created`, which is when the session was
    # *opened* — for a card that's a few seconds earlier, but for a bank transfer it's days
    # earlier, and a purchase row claiming to have been paid days before it was would be
    # wrong in the one place where the record is the evidence.
    paid_at = paid_at or datetime.now(UTC)
    amount_cents = stripe_field(checkout, "amount_total", 0) or 0
    currency = (stripe_field(checkout, "currency") or "usd").lower()

    purchase = await _insert_purchase(
        session, checkout, response.user_id, amount_cents, currency, paid_at
    )
    if purchase is None:
        # A redelivery of an event we've already handled. The purchase is there; the
        # entitlement was written with it, in the same transaction. Nothing to do.
        logger.info("checkout session %s was already recorded; nothing to do", checkout.id)
        return await _await_existing_purchase(session, checkout)

    session.add(
        Entitlement(
            user_id=response.user_id,
            kind="single_report",
            purchase_id=purchase.id,
            response_id=response_id,
            # The entitlement starts when the payment did, not when the row was written.
            # For a delayed payment method those differ by days, and an entitlement that
            # was already valid while the money was still in flight would be wrong.
            valid_from=paid_at,
        )
    )
    await session.flush()

    logger.info(
        "granted a report entitlement for response %s to user %s (session %s)",
        response_id,
        response.user_id,
        checkout.id,
    )
    return purchase


async def _insert_purchase(
    session: AsyncSession,
    checkout: stripe.checkout.Session,
    user_id: UUID,
    amount_cents: int,
    currency: str,
    paid_at: datetime | None,
) -> Purchase | None:
    """The purchase row, or None if this session was already recorded.

    `ON CONFLICT (stripe_checkout_session_id) DO NOTHING` rather than a read-then-write:
    the read-then-write has a window where two deliveries of the same event both see
    nothing and both write. The unique index closes it.
    """
    statement = (
        insert(Purchase)
        .values(
            user_id=user_id,
            stripe_checkout_session_id=checkout.id,
            stripe_payment_intent_id=stripe_field(checkout, "payment_intent"),
            # The Price we created the session with. A session's `line_items` are only
            # populated if the event expanded them, so they can't be relied on here; the
            # configured id is what the session was actually built from.
            stripe_price_id=stripe_client.price_id(),
            amount_cents=amount_cents,
            currency=currency,
            paid_at=paid_at,
        )
        .on_conflict_do_nothing(index_elements=["stripe_checkout_session_id"])
        .returning(Purchase.id)
    )
    purchase_id = (await session.execute(statement)).scalar_one_or_none()
    if purchase_id is None:
        return None

    purchase = await session.get(Purchase, purchase_id)
    assert purchase is not None
    return purchase


async def _await_existing_purchase(
    session: AsyncSession, checkout: stripe.checkout.Session
) -> Purchase:
    """The purchase for a session we've already recorded."""
    purchase = await session.scalar(
        select(Purchase).where(Purchase.stripe_checkout_session_id == checkout.id)
    )
    if purchase is None:  # pragma: no cover - only reachable if a row vanished mid-request
        raise PaymentNotReadyError(f"purchase for checkout session {checkout.id} disappeared")
    return purchase


async def revoke_for_refund(session: AsyncSession, charge: stripe.Charge) -> bool:
    """Revoke the access a refunded payment bought.

    **Both** `purchases.refunded_at` and the entitlement's `revoked_at` are stamped.
    Recording a refund on the purchase alone revokes nothing, because the gate reads
    `entitlements` — a half-done refund leaves the money back and the access still there,
    which is worse than not refunding at all.

    The lookup key is the one that exists: find the purchase by its payment-intent id, then
    find the entitlement by `purchase_id`. There is no payment-intent column on
    `entitlements`; the link is the purchase.

    Returns whether anything was revoked. A refund for a payment we don't know about — a
    one-off charge, a second purchase of the same response — is logged and ignored rather
    than being an error: the money going back is not our problem to fail over.
    """
    payment_intent_id = stripe_field(charge, "payment_intent")
    if not payment_intent_id:
        logger.warning(
            "charge %s was refunded with no payment intent; nothing to revoke",
            stripe_field(charge, "id"),
        )
        return False

    purchase = await session.scalar(
        select(Purchase).where(Purchase.stripe_payment_intent_id == payment_intent_id)
    )
    if purchase is None:
        logger.info(
            "no purchase matches refunded payment intent %s; nothing to revoke", payment_intent_id
        )
        return False

    now = datetime.now(UTC)
    if purchase.refunded_at is None:
        purchase.refunded_at = now

    entitlement = await session.scalar(
        select(Entitlement).where(Entitlement.purchase_id == purchase.id)
    )
    if entitlement is None:
        logger.warning(
            "purchase %s has no entitlement; refund recorded but no access revoked", purchase.id
        )
        await session.flush()
        return False

    if entitlement.revoked_at is None:
        entitlement.revoked_at = now
        logger.info(
            "revoked entitlement %s after refund of purchase %s", entitlement.id, purchase.id
        )

    await session.flush()
    return True
