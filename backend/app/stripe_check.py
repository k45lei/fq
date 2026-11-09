"""Check that Stripe is wired up correctly, before a real purchase.

Run it with `uv run python -m app.stripe_check`. It answers one question — *is the
integration hooked in?* — and answers it in layers, so a failure tells you which layer is
broken rather than just that something is.

The layers, in order:

1. **Is it configured?** The three settings are present. Without them the app still runs
   and the checkout route answers 503, so this is the first thing to check.
2. **Is the key real?** The key is fetched from Stripe's API. A typo, a revoked key or a
   live key pasted where a test key belongs all fail here, and all fail *the same way* at
   the checkout route — which is why this check exists.
3. **Is the Price real, and does it match what we display?** The Price is what the customer
   is actually charged; `report_price_cents` is what the paywall shows. If they disagree,
   the number on the button is a lie.
4. **Is the webhook secret set, and does it match a real endpoint?** The signing secret is
   a *different* credential from the API key, and using the wrong one has a nasty symptom:
   every delivery fails to verify, so a paid purchase never grants access.

Nothing here charges anything, creates a Checkout Session, or writes to the database. It
only reads. The one exception is step 4's "does the endpoint exist" check, which is a
read-only list of the account's webhook endpoints.

Exit status is 0 when everything checks out and 1 when something doesn't, so it can be used
in a script.
"""

import sys
from typing import Any

import stripe

from app.config import get_settings
from app.stripe_client import (
    PaymentsNotConfiguredError,
    client,
    price_id,
    report_price,
    webhook_secret,
)

# Green and red, but readable without colour support.
OK = "  ok  "
BAD = " FAIL "


class Report:
    """The running tally.

    A failed check doesn't stop the run: the point is to show *everything* that's wrong at
    once, because a half-configured Stripe usually has more than one thing missing and
    fixing them one run at a time is slow.
    """

    def __init__(self) -> None:
        self.failures = 0

    def ok(self, message: str) -> None:
        print(f"[{OK}] {message}")

    def fail(self, message: str) -> None:
        self.failures += 1
        print(f"[{BAD}] {message}")

    def note(self, message: str) -> None:
        print(f"        {message}")


def check_configuration(report: Report) -> bool:
    """Are the three settings there?"""
    settings = get_settings()
    print("\n1. Configuration")

    present = True
    for name, value in (
        ("STRIPE_SECRET_KEY", settings.stripe_secret_key),
        ("STRIPE_PRICE_ID", settings.stripe_price_id),
        ("STRIPE_WEBHOOK_SECRET", settings.stripe_webhook_secret),
    ):
        if value:
            report.ok(f"{name} is set")
        else:
            report.fail(f"{name} is not set")
            present = False

    if not present:
        report.note(
            "The app still runs without these. The checkout route answers 503 naming "
            "whichever one is missing."
        )
    return present


def check_the_key(report: Report) -> bool:
    """Does Stripe recognise the key?"""
    print("\n2. The secret key")
    try:
        stripe_client = client()
    except PaymentsNotConfiguredError as error:
        report.fail(str(error))
        return False

    try:
        # The cheapest authenticated call there is. If the key is wrong, revoked, or a
        # live key where a test key was meant, this is where it shows.
        #
        # `balance.retrieve` rather than `accounts.retrieve`: the latter needs an account
        # id, and a connected account isn't what we're testing. The balance is the
        # account's own, and the call is a single GET with no parameters.
        account = stripe_client.v1.balance.retrieve()
    except stripe.StripeError as error:
        report.fail(f"Stripe rejected the key: {error}")
        report.note(
            "Check the key was copied whole, and that it's a test key (sk_test_...) for "
            "test mode. A live key (sk_live_...) won't work against test-mode data."
        )
        return False

    report.ok("Stripe accepted the key")
    report.note(f"account: {getattr(account, 'object', 'balance')}")
    return True


def check_the_price(report: Report) -> None:
    """Does the Price exist, and does it match the display price?"""
    print("\n3. The price")
    try:
        configured_id = price_id()
    except PaymentsNotConfiguredError as error:
        report.fail(str(error))
        return

    try:
        stripe_client = client()
        price = stripe_client.v1.prices.retrieve(configured_id)
    except stripe.StripeError as error:
        report.fail(f"could not read the price {configured_id}: {error}")
        report.note(
            "Is the id a Price (price_...) and not a Product (prod_...)? Is it in the same "
            "mode as your key — a test key needs a test Price?"
        )
        return

    report.ok(f"the price {configured_id} exists")
    unit_amount = getattr(price, "unit_amount", None)
    currency = getattr(price, "currency", None)
    report.note(f"Stripe charges: {unit_amount} {currency}")

    display_cents, display_currency = report_price()
    report.note(f"we display:   {display_cents} {display_currency}")

    if unit_amount == display_cents and (currency or "").lower() == display_currency.lower():
        report.ok("the displayed price matches what Stripe will charge")
    else:
        report.fail("the displayed price does NOT match the Stripe price")
        report.note(
            "The customer would be told one number and charged another. Either set "
            "REPORT_PRICE_CENTS to match the Stripe Price, or change the Stripe Price."
        )


def check_the_webhook(report: Report) -> None:
    """Is the signing secret set, and does a matching endpoint exist?"""
    print("\n4. The webhook")
    try:
        secret = webhook_secret()
    except PaymentsNotConfiguredError as error:
        report.fail(str(error))
        report.note(
            "Without it, deliveries can't be verified and no purchase will ever grant "
            "access — the payment would go through and nothing would unlock."
        )
        return

    report.ok("the signing secret is set")
    if secret.startswith("whsec_"):
        report.ok("it looks like a signing secret (whsec_...)")
    else:
        report.fail("it does not look like a signing secret")
        report.note(
            "A signing secret starts with 'whsec_'. The API key (sk_test_...) is a "
            "different credential and will fail every verification."
        )

    # Does an endpoint exist that points at us? A secret is only useful if there's
    # somewhere for it to sign for.
    try:
        stripe_client = client()
        endpoints = stripe_client.v1.webhook_endpoints.list({"limit": 100})
    except stripe.StripeError as error:
        report.fail(f"could not list webhook endpoints: {error}")
        return

    found: list[Any] = [
        endpoint
        for endpoint in getattr(endpoints, "data", []) or []
        if "stripe/webhook" in (getattr(endpoint, "url", "") or "")
    ]
    if found:
        report.ok(f"{len(found)} endpoint(s) point at a stripe/webhook path")
        for endpoint in found:
            report.note(f"{getattr(endpoint, 'url', '?')} ({getattr(endpoint, 'status', '?')})")
        report.note(
            "Check the endpoint's own signing secret matches the one above. The secret is "
            "shown once, when the endpoint is created."
        )
    else:
        report.note("No registered endpoint points at a stripe/webhook path.")
        report.note(
            "For local testing, run `stripe listen --forward-to localhost:8000/api/stripe/webhook` "
            "and use the whsec_... it prints. For a deployed app, add the endpoint in the "
            "dashboard and copy its signing secret."
        )


def main() -> int:
    print("Stripe wiring check")
    print("=" * 60)

    report = Report()
    configured = check_configuration(report)

    if configured:
        check_the_key(report)
        check_the_price(report)
        check_the_webhook(report)
    else:
        print("\nSkipping the live checks — the configuration isn't complete.")

    print("\n" + "=" * 60)
    if report.failures:
        print(f"{report.failures} problem(s) found.")
        return 1
    print("Everything checks out.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
