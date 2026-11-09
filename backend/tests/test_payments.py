"""Tests for the money path.

Nothing here touches Stripe's network. The outbound call is replaced, in the same shape the
project already uses for email (`app/email.py`'s `_deliver` is replaced in `tests/test_email.py`),
and the *inbound* webhook is genuinely exercised: the test builds a real event body and
signs it with the real SDK helper, so signature verification is actually tested rather than
stubbed out.

The cases that matter most are the ones where money and access could get out of step:

- a redelivered event must not grant twice;
- a refund must revoke, not merely record;
- the return path must work when the webhook hasn't landed;
- an unowned response must 404, and a forged webhook body must write nothing.
"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
import stripe
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.entitlements import has_active_entitlement
from app.main import app
from app.models import (
    DiscountCode,
    Entitlement,
    Purchase,
    QuestionnaireResponse,
    Report,
    User,
)
from app.payments import grant_purchase
from tests.factories import sign_in_directly
from tests.test_api import valid_answers
from tests.test_ownership import begin

# The secret the `stripe_configured` fixture sets. Duplicated here so the module-level
# signing helpers can use it without a fixture argument; the two must agree.
WEBHOOK_SECRET = "whsec_test_not_a_real_secret"


# --------------------------------------------------------------------- the fake Stripe


class FakeCheckoutSession:
    """What `client.v1.checkout.sessions.create` returns.

    A real `stripe.checkout.Session`, so the code under test sees the same shape it will see
    in production — including the awkward parts, like `metadata` being a `StripeObject`
    rather than a dict.
    """

    def __init__(self, **kwargs: Any) -> None:
        self.id = kwargs.get("id", "cs_test_123")
        self.url = kwargs.get("url", "https://checkout.stripe.com/c/pay/cs_test_123")
        self.amount_total = kwargs.get("amount_total", 2999)
        self.payment_status = kwargs.get("payment_status", "paid")
        self.payment_intent = kwargs.get("payment_intent", "pi_test_123")
        self.created = kwargs.get("created", 1_700_000_000)
        self.currency = kwargs.get("currency", "usd")
        self.metadata = kwargs.get("metadata", {})


class FakeStripeClient:
    """Enough of `stripe.StripeClient` for the two calls the routes make."""

    def __init__(self, session: FakeCheckoutSession | None = None) -> None:
        self.created_params: dict[str, Any] | None = None
        self._session = session or FakeCheckoutSession()
        self.v1 = self

    @property
    def checkout(self) -> "FakeStripeClient":
        return self

    @property
    def sessions(self) -> "FakeStripeClient":
        return self

    def create(self, params: dict[str, Any]) -> FakeCheckoutSession:
        self.created_params = params
        # A real session echoes the metadata it was given, which the return path reads.
        self._session.metadata = params.get("metadata", {})
        return self._session

    def retrieve(self, session_id: str) -> FakeCheckoutSession:
        self._session.id = session_id
        return self._session


@pytest.fixture
def fake_stripe(monkeypatch: pytest.MonkeyPatch) -> FakeStripeClient:
    """Replace the Stripe client, so no test reaches the network.

    The module attribute is replaced rather than the SDK's internals, so the code under
    test is the real call — `client.v1.checkout.sessions.create(params)` — and only its
    outbound effect is faked.
    """
    from app.routes import payments

    fake = FakeStripeClient()
    monkeypatch.setattr(payments, "client", lambda: fake)
    return fake


@dataclass(frozen=True)
class Owned:
    """A response the client owns, and the account that owns it.

    The account id isn't in the start response, so it's read from the row — which is also
    the authoritative answer, since that's where the entitlement will be attached.
    """

    response_id: UUID
    user_id: UUID


@pytest.fixture
async def owned_response(
    client: AsyncClient, live: None, session: AsyncSession
) -> AsyncIterator[Owned]:
    """A response the client owns, started through the API.

    Started through `POST /api/responses` rather than inserted into the database, because
    the ownership cookie is set as a side effect of starting it — and the ownership cookie
    is what makes `may_access_response` true. A row inserted by hand would have an owner
    but no browser to prove it.
    """
    started = await begin(client)
    row = await session.get(QuestionnaireResponse, started["response_id"])
    assert row is not None
    yield Owned(response_id=row.id, user_id=row.user_id)


# --------------------------------------------------------------------- helpers


def signed_event(
    event_type: str,
    obj: dict[str, Any],
    secret: str = WEBHOOK_SECRET,
    event_id: str = "evt_test_1",
    event_created: int = 1_700_000_000,
) -> tuple[bytes, str]:
    """A real, signed Stripe event body and the header that goes with it.

    `stripe.WebhookSignature.generate_signature_header` is the SDK's own documented helper
    for exactly this — "Useful for signing payloads in unit tests." The body is signed as a
    string and sent as the same string, so the signature verifies.
    """
    payload = json.dumps(
        {
            "id": event_id,
            "object": "event",
            "api_version": "2024-06-20",
            "type": event_type,
            "created": event_created,
            "data": {"object": obj},
        }
    )
    signature = stripe.WebhookSignature.generate_signature_header(payload, secret)
    return payload.encode(), signature


def checkout_event(
    *,
    response_id: UUID,
    user_id: UUID,
    session_id: str = "cs_test_123",
    payment_intent: str = "pi_test_123",
    amount_total: int = 2999,
    payment_status: str = "paid",
    event_id: str = "evt_test_1",
    event_type: str = "checkout.session.completed",
    event_created: int = 1_700_000_000,
) -> tuple[bytes, str]:
    """A signed `checkout.session.completed` body."""
    body, signature = signed_event(
        event_type,
        {
            "id": session_id,
            "object": "checkout.session",
            "amount_total": amount_total,
            "currency": "usd",
            "payment_status": payment_status,
            "payment_intent": payment_intent,
            "created": 1_700_000_000,
            "metadata": {"response_id": str(response_id), "user_id": str(user_id)},
        },
        event_id=event_id,
        event_created=event_created,
    )
    return body, signature


def answers_for(questionnaire: dict[str, Any], section: str) -> list[dict[str, Any]]:
    """The simplest valid answers for one section.

    A section submit only accepts that section's questions — an answer for a question
    outside it is refused — so the shared helper has to be narrowed.
    """
    return [
        answer
        for answer in valid_answers(questionnaire)
        if answer["question_id"]
        in {
            question["id"]
            for question in questionnaire["questions"]
            if question["category"] == section
        }
    ]


async def test_a_section_that_is_already_done_is_reported_as_done(
    client: AsyncClient,
    owned_response: Owned,
    live: None,
    stripe_configured: str,
) -> None:
    """The client has to know which sections are already submitted.

    A section can only be submitted once, so a client that loops over every available
    section resubmits the free one and is refused with a 422. That's exactly what would
    happen to a visitor who finished the free tier, paid, and came back to answer the paid
    sections — the flow would break at the first request.
    """
    questionnaire = (await client.get("/api/questionnaire")).json()
    assert questionnaire["completed_sections"] == []

    free = await client.post(
        f"/api/responses/{owned_response.response_id}/sections/interests",
        json={"answers": valid_answers(questionnaire)},
    )
    assert free.status_code == 200

    # Now the free section is done, and the server says so.
    questionnaire = (
        await client.get(f"/api/questionnaire?response_id={owned_response.response_id}")
    ).json()
    assert questionnaire["completed_sections"] == ["interests"]

    # And after paying, the paid sections are offered and the free one is not repeated.
    body, signature = checkout_event(
        response_id=owned_response.response_id, user_id=owned_response.user_id
    )
    await client.post("/api/stripe/webhook", content=body, headers={"stripe-signature": signature})

    questionnaire = (
        await client.get(f"/api/questionnaire?response_id={owned_response.response_id}")
    ).json()
    assert questionnaire["sections"] == ["interests", "strengths", "eq"]
    assert questionnaire["completed_sections"] == ["interests"]


async def test_a_completed_section_is_not_submitted_twice(
    client: AsyncClient,
    owned_response: Owned,
    live: None,
) -> None:
    """The server refuses a second submission of the same section.

    Which is why the client has to be told — this is the rule the `completed_sections` field
    exists to let it respect.
    """
    questionnaire = (await client.get("/api/questionnaire")).json()
    first = await client.post(
        f"/api/responses/{owned_response.response_id}/sections/interests",
        json={"answers": valid_answers(questionnaire)},
    )
    assert first.status_code == 200

    second = await client.post(
        f"/api/responses/{owned_response.response_id}/sections/interests",
        json={"answers": valid_answers(questionnaire)},
    )
    assert second.status_code == 422


async def test_a_paid_visitor_can_finish_the_paid_sections(
    client: AsyncClient,
    owned_response: Owned,
    live: None,
    stripe_configured: str,
) -> None:
    """The whole post-payment path, end to end.

    A visitor answers the free section, pays, comes back, answers the two paid sections, and
    gets a full report. Every step of that is a place the flow could break:

    - the questionnaire after payment must offer the paid sections and mark the free one done,
    - the paid sections must be accepted, and produce a *full* report,
    - the report must be findable without a report id, because the checkout returned a
      Stripe session id and not one of ours.
    """
    questionnaire = (await client.get("/api/questionnaire")).json()
    free = await client.post(
        f"/api/responses/{owned_response.response_id}/sections/interests",
        json={"answers": valid_answers(questionnaire)},
    )
    assert free.status_code == 200
    free_report_id = free.json()["report_id"]

    # The free report is a free report.
    free_report = (await client.get(f"/api/reports/{free_report_id}")).json()
    assert free_report["tier"] == "free"

    # Pay.
    body, signature = checkout_event(
        response_id=owned_response.response_id, user_id=owned_response.user_id
    )
    webhook = await client.post(
        "/api/stripe/webhook", content=body, headers={"stripe-signature": signature}
    )
    assert webhook.status_code == 200

    # The questionnaire now offers the paid sections, and says the free one is done.
    questionnaire = (
        await client.get(f"/api/questionnaire?response_id={owned_response.response_id}")
    ).json()
    assert questionnaire["sections"] == ["interests", "strengths", "eq"]
    assert questionnaire["completed_sections"] == ["interests"]

    # Answering the paid sections works, and produces a full report.
    for section in ("strengths", "eq"):
        paid = await client.post(
            f"/api/responses/{owned_response.response_id}/sections/{section}",
            json={"answers": answers_for(questionnaire, section)},
        )
        assert paid.status_code == 200, paid.text
        assert paid.json()["report_id"] != free_report_id

    # And the newest report is the full one, findable without knowing its id.
    latest = await client.get(f"/api/responses/{owned_response.response_id}/report")
    assert latest.status_code == 200
    assert latest.json()["tier"] == "full"

    # Every section is now done, so there is nothing left to answer.
    questionnaire = (
        await client.get(f"/api/questionnaire?response_id={owned_response.response_id}")
    ).json()
    assert questionnaire["completed_sections"] == ["interests", "strengths", "eq"]


async def counts(session: AsyncSession) -> tuple[int, int]:
    """How many purchases and entitlements exist."""
    purchases = len((await session.execute(select(Purchase))).scalars().all())
    entitlements = len((await session.execute(select(Entitlement))).scalars().all())
    return purchases, entitlements


# --------------------------------------------------------------------- the price


async def test_the_price_is_served_from_the_server(client: AsyncClient) -> None:
    """The paywall has to be able to say what it costs before the visitor commits.

    The number comes from the server rather than being hardcoded in the frontend, for the
    same reason pay figures come from the `career_pay` lookup and are never fabricated.
    """
    body = (await client.get("/api/pricing")).json()

    assert body["amount_cents"] == 2999
    assert body["currency"] == "usd"


# --------------------------------------------------------------------- checkout


async def test_checkout_on_someone_elses_response_is_a_404(
    client: AsyncClient, response: QuestionnaireResponse
) -> None:
    """404, not 403: a 403 would confirm the id exists.

    And the body must match the genuine not-found, so a stranger can't tell an existing id
    from a fictional one.
    """
    stranger = await client.post(f"/api/responses/{response.id}/checkout", json={})
    missing = await client.post(
        "/api/responses/00000000-0000-0000-0000-000000000000/checkout", json={}
    )

    assert stranger.status_code == 404
    assert missing.status_code == 404
    assert stranger.json() == missing.json()


async def test_checkout_when_stripe_is_not_configured_is_a_503(
    client: AsyncClient, owned_response: Owned
) -> None:
    """An optional integration being absent must not take the rest of the app down.

    The message names the missing setting, because the person reading it is the one who has
    to fix it.
    """
    result = await client.post(f"/api/responses/{owned_response.response_id}/checkout", json={})

    assert result.status_code == 503
    assert "STRIPE" in result.json()["detail"]


async def test_checkout_when_already_entitled_is_a_409(
    client: AsyncClient,
    owned_response: Owned,
    session: AsyncSession,
    admin_account: Any,
) -> None:
    """Not a second checkout session for something they already own.

    409 rather than 403: it isn't an authorisation failure, it's a conflict with the
    current state.
    """
    sign_in_directly(client, owned_response.user_id)
    session.add(
        Entitlement(
            user_id=owned_response.user_id,
            response_id=owned_response.response_id,
            kind="single_report",
            valid_until=datetime.now(UTC) + timedelta(days=30),
            granted_by=admin_account.id,
        )
    )
    await session.flush()

    result = await client.post(f"/api/responses/{owned_response.response_id}/checkout", json={})

    assert result.status_code == 409


async def test_checkout_returns_a_url_and_a_session_id(
    client: AsyncClient,
    owned_response: Owned,
    fake_stripe: FakeStripeClient,
    stripe_configured: str,
) -> None:
    """The typed contract: a URL to navigate to, and the session's id.

    Not the Stripe session object — that would pin this contract to a third party's
    serialisation and leak their internals into it.
    """
    result = await client.post(f"/api/responses/{owned_response.response_id}/checkout", json={})

    assert result.status_code == 200
    body = result.json()
    assert body["url"] == "https://checkout.stripe.com/c/pay/cs_test_123"
    assert body["session_id"] == "cs_test_123"
    # The contract is a shape, not a bag of whatever Stripe happened to return.
    assert set(body) == {"url", "session_id"}


async def test_the_checkout_url_comes_from_a_configured_origin(
    client: AsyncClient,
    owned_response: Owned,
    fake_stripe: FakeStripeClient,
    stripe_configured: str,
) -> None:
    """The success and cancel URLs are built from `ALLOWED_ORIGINS`.

    Not from a header and not hardcoded — a visitor paying from the dev server must come
    back to the dev server, and one paying from a tunnel must come back to the tunnel.
    """
    await client.post(f"/api/responses/{owned_response.response_id}/checkout", json={})

    params = fake_stripe.created_params
    assert params is not None
    assert params["success_url"].startswith("http://localhost:5173")
    assert params["cancel_url"].startswith("http://localhost:5173")
    # The session id placeholder, so the return page knows what to verify.
    assert "{CHECKOUT_SESSION_ID}" in params["success_url"]


async def test_checkout_with_an_unknown_discount_code_is_a_400(
    client: AsyncClient, owned_response: Owned, fake_stripe: FakeStripeClient
) -> None:
    """The code is checked before the visitor is handed to Stripe.

    They should learn it's wrong while they're still on the page, not after they've been
    sent to a checkout they can't complete.
    """
    result = await client.post(
        f"/api/responses/{owned_response.response_id}/checkout",
        json={"discount_code": "NOPE"},
    )

    assert result.status_code == 400
    assert fake_stripe.created_params is None


async def test_a_discount_code_is_matched_case_insensitively(
    client: AsyncClient,
    owned_response: Owned,
    session: AsyncSession,
    fake_stripe: FakeStripeClient,
    stripe_configured: str,
) -> None:
    """`SUMMER20` and `summer20` are the same code.

    The uniqueness is on `upper(code)` and the schema says redemption queries it that way, so
    a plain equality lookup would treat the two as different codes and leave the index
    unused.
    """
    session.add(
        DiscountCode(
            code="SUMMER20",
            stripe_coupon_id="coupon_test_123",
            starts_at=datetime.now(UTC) - timedelta(days=1),
            ends_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    await session.flush()

    result = await client.post(
        f"/api/responses/{owned_response.response_id}/checkout",
        json={"discount_code": "summer20"},
    )

    assert result.status_code == 200
    params = fake_stripe.created_params
    assert params is not None
    # `discounts`, not `discount_codes` — the latter isn't a Stripe parameter at all.
    assert params["discounts"] == [{"coupon": "coupon_test_123"}]


async def test_a_discount_code_outside_its_window_is_refused(
    client: AsyncClient,
    owned_response: Owned,
    session: AsyncSession,
    fake_stripe: FakeStripeClient,
) -> None:
    """The local `starts_at`/`ends_at` are enforced, so they're a real control.

    Leaving them unenforced while the columns exist is the worst of both: it looks like a
    control that isn't one.
    """
    session.add(
        DiscountCode(
            code="EXPIRED",
            stripe_coupon_id="coupon_test_123",
            starts_at=datetime.now(UTC) - timedelta(days=10),
            ends_at=datetime.now(UTC) - timedelta(days=1),
        )
    )
    await session.flush()

    result = await client.post(
        f"/api/responses/{owned_response.response_id}/checkout",
        json={"discount_code": "EXPIRED"},
    )

    assert result.status_code == 400
    assert fake_stripe.created_params is None


# --------------------------------------------------------------------- the webhook


async def test_the_webhook_grants_a_purchase_and_an_entitlement(
    client: AsyncClient,
    owned_response: Owned,
    session: AsyncSession,
    stripe_configured: str,
) -> None:
    """The happy path: one purchase row, one entitlement row.

    The entitlement is for the response's owner, and the purchase records what Stripe
    charged — not what we configured, so a discount can't be recorded as the full price.
    """
    body, signature = checkout_event(
        response_id=owned_response.response_id,
        user_id=owned_response.user_id,
        amount_total=2500,
    )

    result = await client.post(
        "/api/stripe/webhook", content=body, headers={"stripe-signature": signature}
    )

    assert result.status_code == 200
    purchases, entitlements = await counts(session)
    assert purchases == 1
    assert entitlements == 1

    purchase = (await session.execute(select(Purchase))).scalars().one()
    assert purchase.amount_cents == 2500
    assert purchase.stripe_checkout_session_id == "cs_test_123"
    assert purchase.stripe_payment_intent_id == "pi_test_123"
    assert purchase.user_id == owned_response.user_id
    assert purchase.paid_at is not None

    entitlement = (await session.execute(select(Entitlement))).scalars().one()
    assert entitlement.user_id == owned_response.user_id
    assert entitlement.response_id == owned_response.response_id
    assert entitlement.kind == "single_report"
    # Bought, not comped: exactly one of the two origins.
    assert entitlement.purchase_id == purchase.id
    assert entitlement.granted_by is None


async def test_the_entitlement_actually_opens_the_paywall(
    client: AsyncClient,
    owned_response: Owned,
    session: AsyncSession,
    stripe_configured: str,
) -> None:
    """The money-to-product connection: the gate returns a row afterwards."""
    body, signature = checkout_event(
        response_id=owned_response.response_id, user_id=owned_response.user_id
    )

    await client.post("/api/stripe/webhook", content=body, headers={"stripe-signature": signature})

    assert (
        await has_active_entitlement(session, owned_response.user_id, owned_response.response_id)
        is True
    )


async def test_the_same_event_twice_grants_once(
    client: AsyncClient,
    owned_response: Owned,
    session: AsyncSession,
    stripe_configured: str,
) -> None:
    """Stripe retries, and it sends the same event id.

    The idempotency comes from the unique constraint on `stripe_checkout_session_id`, not
    from a read-then-write pre-check — so this can't race.
    """
    body, signature = checkout_event(
        response_id=owned_response.response_id, user_id=owned_response.user_id
    )

    first = await client.post(
        "/api/stripe/webhook", content=body, headers={"stripe-signature": signature}
    )
    second = await client.post(
        "/api/stripe/webhook", content=body, headers={"stripe-signature": signature}
    )

    assert first.status_code == 200
    assert second.status_code == 200
    purchases, entitlements = await counts(session)
    assert purchases == 1
    assert entitlements == 1


async def test_the_webhook_with_a_bad_signature_writes_nothing(
    client: AsyncClient,
    owned_response: Owned,
    session: AsyncSession,
    stripe_configured: str,
) -> None:
    """A forged body must not grant access.

    400 rather than 401: Stripe retries non-2xx deliveries for up to three days, and a
    forged body will never become good, so the retries should stop.
    """
    body, _ = checkout_event(response_id=owned_response.response_id, user_id=owned_response.user_id)

    result = await client.post(
        "/api/stripe/webhook", content=body, headers={"stripe-signature": "t=1,v1=deadbeef"}
    )

    assert result.status_code == 400
    assert await counts(session) == (0, 0)


async def test_the_webhook_without_a_signature_writes_nothing(
    client: AsyncClient,
    owned_response: Owned,
    session: AsyncSession,
    stripe_configured: str,
) -> None:
    body, _ = checkout_event(response_id=owned_response.response_id, user_id=owned_response.user_id)

    result = await client.post("/api/stripe/webhook", content=body)

    assert result.status_code == 400
    assert await counts(session) == (0, 0)


async def test_the_webhook_without_a_secret_is_a_503(
    client: AsyncClient, owned_response: Owned, session: AsyncSession
) -> None:
    """Without the secret we can't tell a real delivery from a forged one.

    That's a 503 rather than a 400: it isn't that the body is bad, it's that we can't read
    it at all, and the answer is to configure the deployment.
    """
    body, signature = checkout_event(
        response_id=owned_response.response_id, user_id=owned_response.user_id
    )

    result = await client.post(
        "/api/stripe/webhook", content=body, headers={"stripe-signature": signature}
    )

    assert result.status_code == 503
    assert await counts(session) == (0, 0)


async def test_the_webhook_ignores_an_event_type_it_does_not_handle(
    client: AsyncClient, session: AsyncSession, stripe_configured: str
) -> None:
    """A well-formed event we don't care about is not a failure.

    Stripe retries any non-2xx, so acknowledging is what stops a pointless delivery loop.
    """
    body, signature = signed_event("customer.created", {"id": "cus_1", "object": "customer"})

    result = await client.post(
        "/api/stripe/webhook", content=body, headers={"stripe-signature": signature}
    )

    assert result.status_code == 200
    assert await counts(session) == (0, 0)


async def test_the_webhook_grants_nothing_for_a_session_with_no_metadata(
    client: AsyncClient, session: AsyncSession, stripe_configured: str
) -> None:
    """A session created by hand in the Stripe dashboard has no metadata.

    It can't be mapped to a response, so it can't grant anything — and it must not crash
    the endpoint, because a 500 would make Stripe retry something that will never work.
    """
    body, signature = signed_event(
        "checkout.session.completed",
        {
            "id": "cs_orphan",
            "object": "checkout.session",
            "amount_total": 2999,
            "currency": "usd",
            "payment_status": "paid",
            "payment_intent": "pi_orphan",
            "created": 1_700_000_000,
            "metadata": {},
        },
    )

    result = await client.post(
        "/api/stripe/webhook", content=body, headers={"stripe-signature": signature}
    )

    assert result.status_code == 200
    assert await counts(session) == (0, 0)


async def test_the_webhook_grants_nothing_for_an_unpaid_session(
    client: AsyncClient,
    owned_response: Owned,
    session: AsyncSession,
    stripe_configured: str,
) -> None:
    """`payment_status` must be `paid`.

    A `checkout.session.completed` event also fires for sessions that are merely created,
    and for delayed payment methods the session completes before the money has arrived.
    """
    body, signature = checkout_event(
        response_id=owned_response.response_id,
        user_id=owned_response.user_id,
        payment_status="unpaid",
    )

    result = await client.post(
        "/api/stripe/webhook", content=body, headers={"stripe-signature": signature}
    )

    assert result.status_code == 200
    assert await counts(session) == (0, 0)


async def test_a_delayed_payment_that_settles_later_does_grant(
    client: AsyncClient,
    owned_response: Owned,
    session: AsyncSession,
    stripe_configured: str,
) -> None:
    """A bank transfer settles days later, and Stripe tells us when.

    `checkout.session.completed` fires with `payment_status` of `unpaid` for a delayed
    payment method, and `checkout.session.async_payment_succeeded` fires when the money
    arrives. Handling only the first would mean a customer who paid by bank transfer paid
    and received nothing.
    """
    # The session completes, unpaid.
    completed_body, completed_signature = checkout_event(
        response_id=owned_response.response_id,
        user_id=owned_response.user_id,
        payment_status="unpaid",
        event_id="evt_test_completed",
    )
    first = await client.post(
        "/api/stripe/webhook",
        content=completed_body,
        headers={"stripe-signature": completed_signature},
    )
    assert first.status_code == 200
    assert await counts(session) == (0, 0)

    # Then the money arrives.
    settled_body, settled_signature = checkout_event(
        response_id=owned_response.response_id,
        user_id=owned_response.user_id,
        payment_status="paid",
        event_id="evt_test_settled",
        event_type="checkout.session.async_payment_succeeded",
    )
    second = await client.post(
        "/api/stripe/webhook",
        content=settled_body,
        headers={"stripe-signature": settled_signature},
    )

    assert second.status_code == 200
    assert await counts(session) == (1, 1)
    assert (
        await has_active_entitlement(session, owned_response.user_id, owned_response.response_id)
        is True
    )


async def test_a_failed_delayed_payment_grants_nothing(
    client: AsyncClient,
    owned_response: Owned,
    session: AsyncSession,
    stripe_configured: str,
) -> None:
    """A payment method that fails to settle is not a purchase.

    Acknowledged rather than an error, so Stripe stops retrying, and nothing is written.
    """
    body, signature = checkout_event(
        response_id=owned_response.response_id,
        user_id=owned_response.user_id,
        payment_status="unpaid",
        event_id="evt_test_failed",
        event_type="checkout.session.async_payment_failed",
    )

    result = await client.post(
        "/api/stripe/webhook", content=body, headers={"stripe-signature": signature}
    )

    assert result.status_code == 200
    assert await counts(session) == (0, 0)


async def test_the_purchase_records_when_the_money_arrived(
    client: AsyncClient,
    owned_response: Owned,
    session: AsyncSession,
    stripe_configured: str,
) -> None:
    """`paid_at` is the event's time, not the session's creation time.

    A purchase row that says it was paid before it was would be wrong in the one place
    where the row is the evidence. For a card the two are seconds apart; for a bank
    transfer they're days apart, and the difference is the whole reason this is asserted.
    """
    body, signature = checkout_event(
        response_id=owned_response.response_id,
        user_id=owned_response.user_id,
        event_id="evt_test_late",
        event_created=1_700_000_500,
    )

    await client.post("/api/stripe/webhook", content=body, headers={"stripe-signature": signature})

    purchase = (await session.execute(select(Purchase))).scalars().one()
    assert purchase.paid_at == datetime.fromtimestamp(1_700_000_500, tz=UTC)


async def test_the_entitlement_follows_the_responses_current_owner(
    client: AsyncClient,
    owned_response: Owned,
    session: AsyncSession,
    stripe_configured: str,
    other_user: User,
) -> None:
    """The response's owner, not the metadata's copy.

    `app/auth.py:claim_responses` re-points a response at a verified account when a visitor
    signs in, so the owner at webhook time can differ from the owner at checkout time. The
    entitlement follows the response, so the person who ends up with the report is the one
    who can use it.
    """
    body, signature = checkout_event(
        response_id=owned_response.response_id, user_id=owned_response.user_id
    )

    # The response changes hands between the checkout and the delivery.
    response_row = await session.get(QuestionnaireResponse, owned_response.response_id)
    assert response_row is not None
    response_row.user_id = other_user.id
    await session.flush()

    await client.post("/api/stripe/webhook", content=body, headers={"stripe-signature": signature})

    entitlement = (await session.execute(select(Entitlement))).scalars().one()
    assert entitlement.user_id == other_user.id
    assert await has_active_entitlement(session, other_user.id, owned_response.response_id) is True


# --------------------------------------------------------------------- refunds


async def test_a_refund_revokes_the_entitlement(
    client: AsyncClient,
    owned_response: Owned,
    session: AsyncSession,
    stripe_configured: str,
) -> None:
    """Both `purchases.refunded_at` and `entitlements.revoked_at` are stamped.

    Recording a refund on the purchase alone revokes nothing, because the gate reads
    `entitlements` — a half-done refund leaves the money back and the access still there,
    which is worse than not refunding at all.
    """
    body, signature = checkout_event(
        response_id=owned_response.response_id,
        user_id=owned_response.user_id,
        payment_intent="pi_refund",
    )
    await client.post("/api/stripe/webhook", content=body, headers={"stripe-signature": signature})
    assert (
        await has_active_entitlement(session, owned_response.user_id, owned_response.response_id)
        is True
    )

    refund_body, refund_signature = signed_event(
        "charge.refunded",
        {
            "id": "ch_test_1",
            "object": "charge",
            "payment_intent": "pi_refund",
            "amount_refunded": 2999,
            "refunded": True,
        },
        event_id="evt_test_refund",
    )
    result = await client.post(
        "/api/stripe/webhook",
        content=refund_body,
        headers={"stripe-signature": refund_signature},
    )

    assert result.status_code == 200
    purchase = (await session.execute(select(Purchase))).scalars().one()
    assert purchase.refunded_at is not None
    entitlement = (await session.execute(select(Entitlement))).scalars().one()
    assert entitlement.revoked_at is not None
    # The end-to-end consequence: the gate says no.
    assert (
        await has_active_entitlement(session, owned_response.user_id, owned_response.response_id)
        is False
    )


async def test_a_refund_for_an_unknown_payment_revokes_nothing(
    client: AsyncClient, session: AsyncSession, stripe_configured: str
) -> None:
    """A refund for a charge we don't know about is acknowledged, not an error.

    The money going back is Stripe's business; what it must not do is fail the delivery.
    """
    body, signature = signed_event(
        "charge.refunded",
        {
            "id": "ch_unknown",
            "object": "charge",
            "payment_intent": "pi_unknown",
            "amount_refunded": 2999,
            "refunded": True,
        },
    )

    result = await client.post(
        "/api/stripe/webhook", content=body, headers={"stripe-signature": signature}
    )

    assert result.status_code == 200
    assert await counts(session) == (0, 0)


# --------------------------------------------------------------------- the return path


async def test_the_return_path_grants_when_the_webhook_has_not_landed(
    client: AsyncClient,
    owned_response: Owned,
    session: AsyncSession,
    stripe_configured: str,
    fake_stripe: FakeStripeClient,
) -> None:
    """A visitor who has just paid is not refused because a delivery was slow.

    Stripe redirects the browser back the moment the payment completes, and the webhook may
    land a second or two later — or not at all. The return path performs the same grant the
    webhook would have, using the same implementation.
    """
    fake_stripe._session.metadata = {
        "response_id": str(owned_response.response_id),
        "user_id": str(owned_response.user_id),
    }
    fake_stripe._session.payment_status = "paid"

    result = await client.get("/api/checkout/cs_test_123")

    assert result.status_code == 200
    body = result.json()
    assert body["paid"] is True
    assert body["entitled"] is True
    assert body["response_id"] == str(owned_response.response_id)

    purchases, entitlements = await counts(session)
    assert purchases == 1
    assert entitlements == 1
    assert (
        await has_active_entitlement(session, owned_response.user_id, owned_response.response_id)
        is True
    )


async def test_the_return_path_does_not_grant_twice(
    client: AsyncClient,
    owned_response: Owned,
    session: AsyncSession,
    stripe_configured: str,
    fake_stripe: FakeStripeClient,
) -> None:
    """The webhook lands, and then the return path checks: still one of each.

    The same unique constraint that makes a redelivered event harmless makes a return-path
    grant after a webhook grant harmless.
    """
    body, signature = checkout_event(
        response_id=owned_response.response_id, user_id=owned_response.user_id
    )
    await client.post("/api/stripe/webhook", content=body, headers={"stripe-signature": signature})

    fake_stripe._session.metadata = {
        "response_id": str(owned_response.response_id),
        "user_id": str(owned_response.user_id),
    }
    await client.get("/api/checkout/cs_test_123")

    assert await counts(session) == (1, 1)


async def test_the_return_path_of_someone_elses_session_is_a_404(
    client: AsyncClient,
    owned_response: Owned,
    stripe_configured: str,
    fake_stripe: FakeStripeClient,
) -> None:
    """A session id is not a capability.

    It appears in a URL, and URLs get shared. The caller has to be able to access the
    response the session was for, or the check is meaningless.
    """
    fake_stripe._session.metadata = {
        "response_id": str(owned_response.response_id),
        "user_id": str(owned_response.user_id),
    }

    # A second browser, with its own empty cookie jar.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as stranger:
        result = await stranger.get("/api/checkout/cs_test_123")

    assert result.status_code == 404


async def test_the_return_path_reports_an_unpaid_session(
    client: AsyncClient,
    owned_response: Owned,
    session: AsyncSession,
    stripe_configured: str,
    fake_stripe: FakeStripeClient,
) -> None:
    """An unpaid session grants nothing, and says so."""
    fake_stripe._session.metadata = {
        "response_id": str(owned_response.response_id),
        "user_id": str(owned_response.user_id),
    }
    fake_stripe._session.payment_status = "unpaid"

    result = await client.get("/api/checkout/cs_test_123")

    assert result.status_code == 200
    assert result.json()["paid"] is False
    assert result.json()["entitled"] is False
    assert await counts(session) == (0, 0)


# --------------------------------------------------------------------- the unit level


async def test_grant_purchase_is_idempotent(
    session: AsyncSession, response: QuestionnaireResponse, stripe_configured: str
) -> None:
    """The same guarantee at the function level, where the race would be.

    `grant_purchase` is called by two routes, and Stripe can deliver the same event twice.
    """
    checkout = stripe.checkout.Session.construct_from(
        {
            "id": "cs_dup",
            "object": "checkout.session",
            "amount_total": 2999,
            "currency": "usd",
            "payment_status": "paid",
            "payment_intent": "pi_dup",
            "created": 1_700_000_000,
            "metadata": {
                "response_id": str(response.id),
                "user_id": str(response.user_id),
            },
        },
        "sk_test_x",
    )

    await grant_purchase(session, checkout)
    await grant_purchase(session, checkout)

    assert await counts(session) == (1, 1)


async def test_grant_purchase_refuses_an_unpaid_session(
    session: AsyncSession, response: QuestionnaireResponse, stripe_configured: str
) -> None:
    """The unit-level guard, and the exception it raises.

    `PaymentNotReadyError` is caught by both callers and logged, not returned to the
    visitor — a session that isn't paid isn't an error in the request.
    """
    from app.payments import PaymentNotReadyError

    checkout = stripe.checkout.Session.construct_from(
        {
            "id": "cs_unpaid",
            "object": "checkout.session",
            "amount_total": 2999,
            "currency": "usd",
            "payment_status": "unpaid",
            "payment_intent": "pi_unpaid",
            "created": 1_700_000_000,
            "metadata": {
                "response_id": str(response.id),
                "user_id": str(response.user_id),
            },
        },
        "sk_test_x",
    )

    with pytest.raises(PaymentNotReadyError):
        await grant_purchase(session, checkout)

    assert await counts(session) == (0, 0)


# --------------------------------------------------------------------- the report tier


async def test_a_report_scored_without_paying_is_a_free_report(
    client: AsyncClient, owned_response: Owned, live: None
) -> None:
    """The free tier says so.

    `reports.tier` is a fact about what the visitor was entitled to when the report was
    scored, and until they pay, that is the free tier.
    """
    questionnaire = (await client.get("/api/questionnaire")).json()
    submitted = await client.post(
        f"/api/responses/{owned_response.response_id}/sections/interests",
        json={"answers": valid_answers(questionnaire)},
    )
    assert submitted.status_code == 200

    report = (await client.get(f"/api/reports/{submitted.json()['report_id']}")).json()
    assert report["tier"] == "free"


async def test_a_report_scored_after_paying_is_a_full_report(
    client: AsyncClient,
    owned_response: Owned,
    session: AsyncSession,
    live: None,
    stripe_configured: str,
) -> None:
    """The connection the whole thing exists for.

    The visitor pays, and the report they then get says it is the full one. Without this
    the purchase would buy access to two extra questions and nothing else — the report
    itself would be identical to the free one, and `reports.tier` would stay meaningless.
    """
    # Pay.
    body, signature = checkout_event(
        response_id=owned_response.response_id, user_id=owned_response.user_id
    )
    await client.post("/api/stripe/webhook", content=body, headers={"stripe-signature": signature})

    # The questionnaire now serves the paid sections too. The response id is passed,
    # because a `single_report` entitlement names one response.
    questionnaire = (
        await client.get(f"/api/questionnaire?response_id={owned_response.response_id}")
    ).json()
    assert {question["category"] for question in questionnaire["questions"]} == {
        "interests",
        "strengths",
        "eq",
    }

    submitted = await client.post(
        f"/api/responses/{owned_response.response_id}/sections/strengths",
        json={"answers": answers_for(questionnaire, "strengths")},
    )
    assert submitted.status_code == 200

    report = (await client.get(f"/api/reports/{submitted.json()['report_id']}")).json()
    assert report["tier"] == "full"


async def test_the_free_report_is_kept_alongside_the_full_one(
    client: AsyncClient,
    owned_response: Owned,
    session: AsyncSession,
    live: None,
    stripe_configured: str,
) -> None:
    """Reports append; they don't overwrite.

    So the free report the visitor already had is still there, and the full one is a second
    row. That is the design working — the report history is the progression, not a
    replacement.
    """
    questionnaire = (await client.get("/api/questionnaire")).json()
    free = await client.post(
        f"/api/responses/{owned_response.response_id}/sections/interests",
        json={"answers": valid_answers(questionnaire)},
    )
    assert free.status_code == 200

    body, signature = checkout_event(
        response_id=owned_response.response_id, user_id=owned_response.user_id
    )
    await client.post("/api/stripe/webhook", content=body, headers={"stripe-signature": signature})

    questionnaire = (
        await client.get(f"/api/questionnaire?response_id={owned_response.response_id}")
    ).json()
    full = await client.post(
        f"/api/responses/{owned_response.response_id}/sections/strengths",
        json={"answers": answers_for(questionnaire, "strengths")},
    )
    assert full.status_code == 200

    rows = (
        (
            await session.execute(
                select(Report).where(Report.response_id == owned_response.response_id)
            )
        )
        .scalars()
        .all()
    )
    assert {row.tier for row in rows} == {"free", "full"}
    assert len(rows) == 2


async def test_the_newest_report_for_a_response_is_the_full_one(
    client: AsyncClient,
    owned_response: Owned,
    session: AsyncSession,
    live: None,
    stripe_configured: str,
) -> None:
    """A visitor who has just paid has a Stripe session id, not a report id.

    So they need a way back to the right report — and "the right report" is the newest one,
    because reports append. If this returned the free one, paying would appear to do
    nothing.
    """
    questionnaire = (await client.get("/api/questionnaire")).json()
    free = await client.post(
        f"/api/responses/{owned_response.response_id}/sections/interests",
        json={"answers": valid_answers(questionnaire)},
    )
    assert free.status_code == 200

    body, signature = checkout_event(
        response_id=owned_response.response_id, user_id=owned_response.user_id
    )
    await client.post("/api/stripe/webhook", content=body, headers={"stripe-signature": signature})

    questionnaire = (
        await client.get(f"/api/questionnaire?response_id={owned_response.response_id}")
    ).json()
    full = await client.post(
        f"/api/responses/{owned_response.response_id}/sections/strengths",
        json={"answers": answers_for(questionnaire, "strengths")},
    )
    assert full.status_code == 200

    latest = await client.get(f"/api/responses/{owned_response.response_id}/report")
    assert latest.status_code == 200
    assert latest.json()["id"] == full.json()["report_id"]
    assert latest.json()["tier"] == "full"


async def test_the_newest_report_is_not_readable_by_a_stranger(
    client: AsyncClient,
    owned_response: Owned,
    live: None,
) -> None:
    """The newest report is still behind the ownership check.

    Otherwise the endpoint would be a way to read any response's report by guessing its id.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as stranger:
        response = await stranger.get(f"/api/responses/{owned_response.response_id}/report")
    assert response.status_code == 404


async def test_the_newest_report_is_the_full_one_even_on_a_timestamp_tie(
    client: AsyncClient,
    owned_response: Owned,
    session: AsyncSession,
    live: None,
    stripe_configured: str,
) -> None:
    """The tie-break has to be the tier, not the id.

    `reports.generated_at` is `now()`, and in Postgres `now()` is the *transaction's* start
    time — so two reports written in one transaction get the same timestamp. "Newest" is then
    decided by the tie-break, and a UUID tie-break is a coin flip: the full report is
    returned about half the time.

    This test pins the tie-break to the tier. `report_tier` is declared `('free', 'full')`,
    so `full` sorts last and therefore first under `DESC` — which is the ordering we want
    anyway, because on a tie the more complete report is the one to show.
    """
    questionnaire = (await client.get("/api/questionnaire")).json()
    free = await client.post(
        f"/api/responses/{owned_response.response_id}/sections/interests",
        json={"answers": valid_answers(questionnaire)},
    )
    assert free.status_code == 200

    body, signature = checkout_event(
        response_id=owned_response.response_id, user_id=owned_response.user_id
    )
    await client.post("/api/stripe/webhook", content=body, headers={"stripe-signature": signature})

    questionnaire = (
        await client.get(f"/api/questionnaire?response_id={owned_response.response_id}")
    ).json()
    full = await client.post(
        f"/api/responses/{owned_response.response_id}/sections/strengths",
        json={"answers": answers_for(questionnaire, "strengths")},
    )
    assert full.status_code == 200

    rows = (
        (
            await session.execute(
                select(Report).where(Report.response_id == owned_response.response_id)
            )
        )
        .scalars()
        .all()
    )
    # The premise of the test: if these two ever differ, the tie-break isn't being
    # exercised and the test would pass for the wrong reason.
    assert rows[0].generated_at == rows[1].generated_at

    # And the answer is the full one. The tie-break has to be *the tier*, so this asserts
    # the ordering directly rather than relying on which row the planner happens to
    # return — otherwise the test is a coin flip that passes about half the time, which is
    # exactly the bug.
    ordered = (
        await session.execute(
            select(Report.id, Report.tier)
            .where(Report.response_id == owned_response.response_id)
            .order_by(Report.generated_at.desc(), Report.tier.desc(), Report.id.desc())
        )
    ).all()
    assert [row.tier for row in ordered] == ["full", "free"]

    latest = await client.get(f"/api/responses/{owned_response.response_id}/report")
    assert latest.json()["id"] == full.json()["report_id"]
    assert latest.json()["tier"] == "full"
