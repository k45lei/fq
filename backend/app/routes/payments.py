"""Payments: checkout, the webhook, and the return-path check.

Three routes, and the order they appear in is the order a purchase happens in:

1. `POST /api/responses/{response_id}/checkout` — the visitor asks to pay. This returns a
   Stripe URL to navigate to; it takes no money itself.
2. `POST /api/stripe/webhook` — Stripe tells us the money arrived, and we write the purchase
   and the entitlement. This is where access is actually granted.
3. `GET /api/checkout/{session_id}` — the browser comes back from Stripe and asks whether
   they have access yet. It verifies the session with Stripe and, if the webhook hasn't
   landed, performs the same grant inline.

**The webhook and the return path share one implementation** (`app/payments.grant_purchase`),
so "a completed checkout becomes access" is written once. A visitor who has just paid must
not be refused because a background delivery was slow — and two implementations of the same
rule would eventually disagree, which is exactly how that happens.

**The webhook trusts nothing it is sent.** The body is verified against the signing secret
before it's parsed, the metadata is checked against the response row, and the amount recorded
is what Stripe says was charged rather than what we configured.
"""

import logging
import time
from datetime import UTC, datetime
from uuid import UUID

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import may_access_response
from app.db import get_session
from app.entitlements import has_active_entitlement
from app.models import DiscountCode, QuestionnaireResponse
from app.origins import origin_for
from app.payments import (
    ASYNC_PAYMENT_FAILED,
    ASYNC_PAYMENT_SUCCEEDED,
    CHARGE_REFUNDED,
    CHECKOUT_COMPLETED,
    MalformedEventError,
    PaymentNotReadyError,
    grant_purchase,
    metadata_value,
    parse_uuid,
    revoke_for_refund,
    stripe_field,
)
from app.schemas import CheckoutOut, CheckoutRequest, CheckoutStatusOut, PricingOut
from app.stripe_client import (
    PaymentsNotConfiguredError,
    checkout_metadata,
    client,
    price_id,
    report_price,
    webhook_secret,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["payments"])

SessionDep = Depends(get_session)

# Where the browser goes after a successful payment, and where it goes if it gives up. Both
# are built from a configured origin — see `app/origins.py`.
CHECKOUT_SUCCESS_PATH = "/checkout/success"
CHECKOUT_CANCEL_PATH = "/checkout/cancelled"

# How long a checkout session stays open. Stripe's own default is 24 hours, and a visitor
# who abandons a payment and comes back a week later should be asked to pay again rather
# than resuming a stale session.
CHECKOUT_EXPIRES_IN_SECONDS = 60 * 60


@router.get("/pricing", response_model=PricingOut)
async def pricing() -> PricingOut:
    """What the report costs, so the paywall can say so before the visitor commits.

    The price lives on the server for the same reason pay figures come from the `career_pay`
    lookup: the number shown must be the number charged, and a hardcoded figure in the
    frontend would be a figure nobody maintains.
    """
    amount_cents, currency = report_price()
    return PricingOut(amount_cents=amount_cents, currency=currency)


@router.post("/responses/{response_id}/checkout", response_model=CheckoutOut)
async def create_checkout(
    response_id: UUID,
    body: CheckoutRequest,
    request: Request,
    session: AsyncSession = SessionDep,
) -> CheckoutOut:
    """Create a Stripe Checkout session for this response.

    The order of the checks matters. Ownership first, and 404 rather than 403: a 403 would
    confirm the id exists, which is what someone enumerating UUIDs wants to learn. Then the
    entitlement, then the configuration — because "you already have this" and "we can't take
    money" are different answers, and the first is the one the visitor needs.
    """
    if not await may_access_response(session, request, response_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no response with that id")

    response = await session.get(QuestionnaireResponse, response_id)
    if response is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no response with that id")

    # Already paid for this one. A conflict with the current state, not an authorisation
    # failure, so 409 rather than 403 — and the frontend can say "you already have this"
    # instead of sending someone to a checkout page for something they own.
    if await has_active_entitlement(session, response.user_id, response_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "you already have the full report for this response"
        )

    discount = None
    if body.discount_code:
        discount = await _find_discount(session, body.discount_code)
        if discount is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "that discount code isn't valid")

    try:
        stripe_client = client()
        price = price_id()
    except PaymentsNotConfiguredError as error:
        logger.warning("checkout unavailable: %s", error)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error

    origin = origin_for(request)
    # `{CHECKOUT_SESSION_ID}` is a placeholder Stripe substitutes with the real id, so the
    # return page knows which session to verify. The cancel URL has no session, because
    # nothing was paid.
    success_url = f"{origin}{CHECKOUT_SUCCESS_PATH}?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{origin}{CHECKOUT_CANCEL_PATH}?response_id={response_id}"

    params: dict[str, object] = {
        "mode": "payment",
        "line_items": [{"price": price, "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata": checkout_metadata(response_id=response_id, user_id=response.user_id),
        "expires_at": int(time.time()) + CHECKOUT_EXPIRES_IN_SECONDS,
    }
    if discount is not None and discount.stripe_coupon_id:
        # `discounts`, not `discount_codes` — the latter is not a Stripe parameter, and the
        # SDK passes unknown parameters straight through to the API, so the mistake only
        # shows up as a 400 from Stripe in production.
        params["discounts"] = [{"coupon": discount.stripe_coupon_id}]

    try:
        checkout = stripe_client.v1.checkout.sessions.create(params)  # type: ignore[arg-type]
    except stripe.error.StripeError as error:
        # Logged with the request id, which is what Stripe support asks for. The client gets
        # a fixed message: a webhook error is for the logs, not for the caller, and Stripe's
        # internal wording isn't ours to publish.
        logger.error(
            "could not create a checkout session: %s (request id %s)",
            error.user_message,
            error.request_id,
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "could not start the payment right now"
        ) from error

    logger.info("created checkout session %s for response %s", checkout.id, response_id)
    return CheckoutOut(url=checkout.url, session_id=checkout.id)


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request, session: AsyncSession = SessionDep) -> dict[str, str]:
    """Stripe's deliveries.

    **The raw body is verified before anything is parsed.** `await request.body()` gives the
    exact bytes Stripe signed; re-serialising parsed JSON would change them and break the
    signature. So the body is read as bytes, verified, and only then interpreted.

    The status codes are chosen for Stripe's behaviour, not for elegance. Stripe retries any
    non-2xx delivery for up to three days, and treats 4xx and 5xx differently: a 400 stops
    the retries, which is what a forged or malformed body deserves, while a 5xx asks Stripe
    to try again, which is what a *legitimate* event we couldn't process deserves.
    """
    try:
        secret = webhook_secret()
    except PaymentsNotConfiguredError as error:
        # Loud, and not a 400: without the secret we can't tell a real delivery from a
        # forged one, so we can't say "this isn't for me" — we can only say we can't read it.
        logger.error("webhook unavailable: %s", error)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error

    signature = request.headers.get("stripe-signature")
    if not signature:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing stripe-signature header")

    body = await request.body()
    try:
        event = stripe.Webhook.construct_event(body, signature, secret)
    except ValueError:
        # Not JSON, or not an event. Nothing to retry.
        logger.warning("webhook received a body that isn't a valid event")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "malformed event") from None
    except stripe.error.SignatureVerificationError:
        # Forged, or signed with the wrong secret. A 400 rather than a 401, so Stripe stops
        # retrying: this body will never be good.
        logger.warning("webhook signature verification failed")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid signature") from None

    try:
        handled = await _handle_event(session, event)
    except MalformedEventError as error:
        # The signature verified, so this really did come from Stripe — but the body isn't
        # an event. There's nothing to acknowledge and nothing that will ever become
        # valid, so it's refused rather than retried for three days.
        logger.warning("webhook received a signed body that isn't an event: %s", error)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "malformed event") from None
    await session.commit()

    logger.info(
        "handled stripe event %s (%s)",
        stripe_field(event, "id"),
        stripe_field(event, "type"),
    )
    return {"received": "ok", "handled": handled}


@router.get("/checkout/{session_id}", response_model=CheckoutStatusOut)
async def checkout_status(
    session_id: str,
    request: Request,
    session: AsyncSession = SessionDep,
) -> CheckoutStatusOut:
    """Whether a checkout has resulted in access, checked on the browser's return.

    Stripe redirects the browser back the moment the payment completes, and the webhook that
    grants access may not have landed yet. Rather than polling or assuming, the return page
    asks here: the session is retrieved from Stripe, and if it's paid and the entitlement
    isn't there yet, the same grant the webhook performs is performed inline.

    **The session id alone is not authority to see anything.** The session's metadata names
    the response, and the response has to be one the caller may access — otherwise anyone
    who guessed a session id could claim a purchase. The check is the same `may_access_response`
    every other route uses.
    """
    try:
        stripe_client = client()
    except PaymentsNotConfiguredError as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error

    try:
        checkout = stripe_client.v1.checkout.sessions.retrieve(session_id)
    except stripe.error.StripeError as error:
        logger.error(
            "could not retrieve checkout session %s: %s (request id %s)",
            session_id,
            error.user_message,
            error.request_id,
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "could not check the payment right now"
        ) from error

    response_id = _response_id_of(checkout)
    if response_id is None:
        return CheckoutStatusOut(session_id=session_id, paid=False, entitled=False)

    # The caller must be able to access the response the session was for. A session id is
    # not a capability: it appears in a URL, and URLs get shared.
    if not await may_access_response(session, request, response_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no response with that id")

    paid = stripe_field(checkout, "payment_status") == "paid"
    entitled = await has_active_entitlement(
        session, await _owner_of(session, response_id), response_id
    )

    if paid and not entitled:
        # The webhook hasn't landed. Do the same work it would have, so the visitor who has
        # just paid isn't refused because a background delivery was slow.
        try:
            await grant_purchase(session, checkout)
            await session.commit()
            entitled = True
        except PaymentNotReadyError as error:
            logger.warning("could not grant on return for session %s: %s", session_id, error)

    return CheckoutStatusOut(
        session_id=session_id,
        paid=paid,
        entitled=entitled,
        response_id=response_id,
    )


# --------------------------------------------------------------------- helpers


def _response_id_of(checkout: stripe.checkout.Session) -> UUID | None:
    """The response a session was for, from its metadata."""
    return parse_uuid(metadata_value(checkout, "response_id"))


async def _owner_of(session: AsyncSession, response_id: UUID) -> UUID:
    """The response's owner.

    Read from the row rather than the session's metadata, because the owner can change
    between the checkout and now (a visitor signing in re-points a response at a verified
    account), and the gate looks entitlements up by user.
    """
    owner = (
        await session.execute(
            select(QuestionnaireResponse.user_id).where(QuestionnaireResponse.id == response_id)
        )
    ).scalar_one_or_none()
    if owner is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no response with that id")
    return owner


async def _find_discount(session: AsyncSession, code: str) -> DiscountCode | None:
    """The discount code, if it's one this product accepts and it's in date.

    The lookup is on `upper(code)`, because the uniqueness is on `upper(code)` and the
    schema says redemption queries it that way. A plain equality lookup would make the
    index unusable and would treat `SUMMER20` and `summer20` as different codes.

    The local `starts_at`/`ends_at` are enforced here: a code outside its window is
    refused, so the rule is a real control rather than a column that looks like one.
    `max_redemptions` is *not* enforced — see `docs/plan.md`.
    """
    now = datetime.now(UTC)
    return await session.scalar(
        select(DiscountCode).where(
            func.upper(DiscountCode.code) == code.strip().upper(),
            (DiscountCode.starts_at.is_(None)) | (DiscountCode.starts_at <= now),
            (DiscountCode.ends_at.is_(None)) | (DiscountCode.ends_at > now),
        )
    )


async def _handle_event(session: AsyncSession, event: stripe.Event) -> str:
    """Act on one event. Returns what was done, for the log line."""
    # Read the type defensively: a body can be valid JSON with a valid signature and still
    # not be an event, and the SDK raises `AttributeError` for a missing `type` rather than
    # handing back `None`. Unhandled, that became a 500, and Stripe retried it for three
    # days.
    event_type = stripe_field(event, "type")
    event_id = stripe_field(event, "id")
    if not event_type:
        # A body with no `type` isn't an event at all, whatever it was signed with. This
        # is different from an event of an unknown type, which is well-formed and is
        # ignored below.
        raise MalformedEventError("event has no type")

    if event_type in (CHECKOUT_COMPLETED, ASYNC_PAYMENT_SUCCEEDED):
        checkout = stripe_field(stripe_field(event, "data"), "object")
        if checkout is None:
            logger.warning("stripe event %s has no data object; nothing to grant", event_id)
            return "not-granted"
        try:
            await grant_purchase(session, checkout, paid_at=_event_time(event))
        except PaymentNotReadyError as error:
            # Not an error: the event is legitimate but the money hasn't arrived, or the
            # session is one we can't map to a response. Logged and acknowledged — a 5xx
            # would make Stripe retry something that will never become valid.
            logger.warning("not granting from %s: %s", event_id, error)
            return "not-granted"
        return "granted"

    if event_type == ASYNC_PAYMENT_FAILED:
        # The money never arrived for a delayed payment method. There is nothing to revoke
        # — no purchase was written — but it's worth a line in the log, because a customer
        # who was told it would settle and then didn't is a support ticket.
        logger.warning("async payment failed for checkout session %s", event_id)
        return "payment-failed"

    if event_type == CHARGE_REFUNDED:
        charge = stripe_field(stripe_field(event, "data"), "object")
        if charge is None:
            logger.warning("stripe event %s has no data object; nothing to revoke", event_id)
            return "nothing-to-revoke"
        revoked = await revoke_for_refund(session, charge)
        return "revoked" if revoked else "nothing-to-revoke"

    # An event type we don't handle. Acknowledged and ignored: Stripe retries any non-2xx,
    # and a well-formed event we don't care about is not a failure.
    logger.info("ignoring stripe event %s of type %s", event_id, event_type)
    return "ignored"


def _event_time(event: stripe.Event) -> datetime | None:
    """When the event happened, as an aware datetime.

    The event's own timestamp, not the session's creation time. For a delayed payment
    method these differ by days, and `purchases.paid_at` is the record of when the money
    arrived.
    """
    created = getattr(event, "created", None)
    if not isinstance(created, int):
        return None
    return datetime.fromtimestamp(created, tz=UTC)
