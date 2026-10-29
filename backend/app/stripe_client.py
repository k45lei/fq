"""Stripe: one client, one price, one metadata builder.

Everything that talks to Stripe goes through this module, so the key is read in one place, a
missing key is one named error rather than an `AttributeError` somewhere downstream, and the
price and the metadata are built the same way every time.

**The price has two homes, and only one of them is authoritative.** Stripe's Price is what
the customer is actually charged, so `stripe_price_id` decides the money. `report_price_cents`
is what the API *displays* before checkout — a visitor has to be told the cost before they
commit to a Stripe page, and the display can't come from Stripe without a round trip to
fetch the Price. The two must agree; a mismatch is a deployment mistake, and the rollout
checklist says so. **The recorded purchase always uses what Stripe charged**
(`amount_total`), never the display figure, so a mismatch can't corrupt the books — it can
only mislead the customer, which is why it's a checklist item rather than a code path.

**The metadata is the webhook's only memory.** Stripe doesn't know what a user bought; it
knows a Checkout Session was created and carries whatever we put in `metadata`. So the
metadata has to carry enough to reconstruct the purchase: which response, and who paid.
Both, always — the response id alone isn't enough, because the response's owner can change
(a visitor signing in re-points a response at a verified account; see
`app/auth.py:claim_responses`).
"""

from uuid import UUID

import stripe

from app.config import get_settings

# The kind of entitlement a per-report purchase issues. The other kind, `window`, is bought
# over a period instead of for one assessment, and Phase 2 doesn't build it — see
# `docs/plan.md`.
REPORT_ENTITLEMENT_KIND = "single_report"


class PaymentsNotConfiguredError(RuntimeError):
    """Stripe isn't set up, so this feature is unavailable.

    Mirrors `EmailNotConfiguredError` in `app/email.py` and `GoogleNotConfiguredError` in
    `app/auth.py`. The pattern is the same everywhere: the integration is optional, the app
    starts without it, and the route that needs it answers 503 with a message naming the
    setting that's missing.
    """


def _secret_key() -> str:
    settings = get_settings()
    if not settings.stripe_secret_key:
        raise PaymentsNotConfiguredError(
            "STRIPE_SECRET_KEY is not set, so payments are unavailable."
        )
    return settings.stripe_secret_key.get_secret_value()


def _price_id() -> str:
    settings = get_settings()
    if not settings.stripe_price_id:
        raise PaymentsNotConfiguredError(
            "STRIPE_PRICE_ID is not set, so the report price is unknown."
        )
    return settings.stripe_price_id


def webhook_secret() -> str:
    """The secret Stripe signs webhook deliveries with.

    Separate from the API key on purpose: they are different credentials with different
    blast radii, and a deployment may register its webhook endpoint before it can create a
    checkout session.
    """
    settings = get_settings()
    if not settings.stripe_webhook_secret:
        raise PaymentsNotConfiguredError(
            "STRIPE_WEBHOOK_SECRET is not set, so webhook deliveries cannot be verified."
        )
    return settings.stripe_webhook_secret.get_secret_value()


def client() -> stripe.StripeClient:
    """The Stripe client, built from the configured key.

    `stripe.StripeClient` rather than the old module-level `stripe.api_key`: the client holds
    the key, so it can't be accidentally used by code that meant to be unauthenticated, and
    two clients with two keys can't exist by accident.

    Note the path: `client.v1.checkout.sessions`, not `client.checkout.sessions`. The
    shorter form is deprecated in the SDK and emits a `DeprecationWarning`.
    """
    return stripe.StripeClient(_secret_key())


def price_id() -> str:
    """The Stripe Price a report costs."""
    return _price_id()


def report_price() -> tuple[int, str]:
    """What the report costs, for display: cents and a currency code.

    Read from settings rather than hardcoded, so a price change is a deploy — and so the
    number the API shows can't drift from a constant in the code.
    """
    settings = get_settings()
    return settings.report_price_cents, settings.report_price_currency


def checkout_metadata(*, response_id: UUID, user_id: UUID) -> dict[str, str]:
    """What the webhook reads back to reconstruct the purchase.

    Both ids, always. The response id says *what* was bought; the user id says *who* paid,
    and is a cross-check rather than the source of truth — the webhook reads the response row
    for the owner, because a response can change hands between the checkout and the delivery
    (see the module docstring).
    """
    return {"response_id": str(response_id), "user_id": str(user_id)}
