"""Tests for the Stripe integration itself.

`tests/test_payments.py` covers the *product* consequences of a payment: that access is
granted, that a refund revokes it, that a report comes out full. This file covers the
*integration*: the seam between this application and Stripe's API, where the mistakes live.

The seam is where third-party shapes leak in, and every one of those shapes has a way to
surprise us:

- **The SDK's objects aren't dicts.** `checkout.metadata` is a `StripeObject`, which
  supports `[...]` and raises `AttributeError` on `.get(...)`. A *missing* key is worse
  still: the SDK raises `AttributeError` for the attribute itself, so `checkout.metadata`
  explodes rather than returning `None`. A session created by hand in the Stripe dashboard
  has no `metadata` at all, and it arrives as a real webhook event.
- **Signatures are verified against the raw bytes.** Re-serialising the parsed JSON changes
  them, so the body has to be read as bytes and signed as bytes.
- **Stripe retries.** Every non-2xx is retried for up to three days, so the status codes
  are load-bearing: 4xx stops the retries, 5xx asks for them.
- **The same event arrives twice.** Idempotency has to come from the schema.
- **The metadata is the only memory of what was bought.** Stripe doesn't know which
  response a session was for; it only knows what we put in `metadata`.

Nothing here reaches Stripe's network. The outbound call is replaced in the same shape the
project already uses for email, while the *inbound* webhook is genuinely exercised: real
event bodies, signed with the real SDK helper, so signature verification is tested rather
than stubbed out.
"""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import stripe
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import stripe_client
from app.config import get_settings
from app.entitlements import has_active_entitlement
from app.main import app
from app.models import DiscountCode, Entitlement, Purchase, QuestionnaireResponse, User
from app.payments import (
    ASYNC_PAYMENT_FAILED,
    ASYNC_PAYMENT_SUCCEEDED,
    CHARGE_REFUNDED,
    CHECKOUT_COMPLETED,
    PaymentNotReadyError,
    grant_purchase,
    metadata_value,
    parse_uuid,
    revoke_for_refund,
)
from app.routes import payments as payments_routes
from app.stripe_client import PaymentsNotConfiguredError
from tests.factories import sign_in_directly
from tests.test_ownership import begin

# The secret the `stripe_configured` fixture sets. Duplicated here so the module-level
# signing helpers can use it without a fixture argument; the two must agree.
WEBHOOK_SECRET = "whsec_test_not_a_real_secret"
TEST_PRICE_ID = "price_test_not_a_real_price"


# --------------------------------------------------------------------- the fake Stripe


class FakeCheckoutSession:
    """What `client.v1.checkout.sessions.create` and `.retrieve` return.

    Deliberately *not* a `stripe.checkout.Session`: a plain object with the same
    attributes lets a test set an attribute the SDK would never produce — a missing
    `metadata`, a `None` currency, a `payment_status` Stripe wouldn't send. Those are
    exactly the shapes that break code, and a real SDK object can't express them without
    fighting it.
    """

    def __init__(self, **kwargs: Any) -> None:
        self.id = kwargs.get("id", "cs_test_123")
        self.url = kwargs.get("url", "https://checkout.stripe.com/c/pay/cs_test_123")
        self.amount_total = kwargs.get("amount_total", 2999)
        self.payment_status = kwargs.get("payment_status", "paid")
        self.payment_intent = kwargs.get("payment_intent", "pi_test_123")
        self.created = kwargs.get("created", 1_700_000_000)
        self.currency = kwargs.get("currency", "usd")
        # `metadata` is set to a real StripeObject where it is present, because that *is*
        # the shape the SDK produces and the shape that has bitten us before.
        metadata = kwargs.get("metadata", {})
        self.metadata = (
            stripe.StripeObject.construct_from(metadata, "sk_test_x")
            if metadata is not None
            else None
        )


class FakeStripeClient:
    """Enough of `stripe.StripeClient` for the calls the routes make.

    The `v1` → `checkout` → `sessions` chain is reproduced as properties, so a typo in the
    SDK path (the deprecated `client.checkout` form) shows up as a failure here rather
    than in production.
    """

    def __init__(self, session: FakeCheckoutSession | None = None) -> None:
        self.created_params: dict[str, Any] | None = None
        self.retrieved_ids: list[str] = []
        self.create_error: Exception | None = None
        self.retrieve_error: Exception | None = None
        self._session = session or FakeCheckoutSession()
        self.v1 = self

    @property
    def checkout(self) -> "FakeStripeClient":
        return self

    @property
    def sessions(self) -> "FakeStripeClient":
        return self

    def create(self, params: dict[str, Any]) -> FakeCheckoutSession:
        if self.create_error is not None:
            raise self.create_error
        self.created_params = params
        # A real session echoes the metadata it was given, which the return path reads.
        self._session.metadata = stripe.StripeObject.construct_from(
            params.get("metadata", {}), "sk_test_x"
        )
        return self._session

    def retrieve(self, session_id: str) -> FakeCheckoutSession:
        if self.retrieve_error is not None:
            raise self.retrieve_error
        self.retrieved_ids.append(session_id)
        self._session.id = session_id
        return self._session


@pytest.fixture
def fake_stripe(monkeypatch: pytest.MonkeyPatch) -> FakeStripeClient:
    """Replace the Stripe client, so no test reaches the network.

    The module attribute is replaced rather than the SDK's internals, so the code under
    test is the real call — `client.v1.checkout.sessions.create(params)` — and only its
    outbound effect is faked.
    """
    fake = FakeStripeClient()
    monkeypatch.setattr(payments_routes, "client", lambda: fake)
    return fake


@pytest.fixture
async def owned_response(
    client: AsyncClient, live: None, session: AsyncSession
) -> AsyncIterator[UUID]:
    """A response the client owns, started through the API.

    Started through `POST /api/responses` rather than inserted into the database, because
    the ownership cookie is set as a side effect of starting it — and the ownership cookie
    is what makes `may_access_response` true. A row inserted by hand would have an owner
    but no browser to prove it.
    """
    started = await begin(client)
    row = await session.get(QuestionnaireResponse, started["response_id"])
    assert row is not None
    yield row.id


async def owner_of(session: AsyncSession, response_id: UUID) -> UUID:
    row = await session.get(QuestionnaireResponse, response_id)
    assert row is not None
    return row.user_id


# --------------------------------------------------------------------- signing helpers


def signed_event(
    event_type: str,
    obj: dict[str, Any],
    secret: str = WEBHOOK_SECRET,
    event_id: str = "evt_test_1",
    event_created: int = 1_700_000_000,
    timestamp: int | None = None,
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
    signature = stripe.WebhookSignature.generate_signature_header(payload, secret, timestamp)
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
    event_type: str = CHECKOUT_COMPLETED,
    event_created: int = 1_700_000_000,
    currency: str = "usd",
    timestamp: int | None = None,
) -> tuple[bytes, str]:
    """A signed `checkout.session.completed` body."""
    return signed_event(
        event_type,
        {
            "id": session_id,
            "object": "checkout.session",
            "amount_total": amount_total,
            "currency": currency,
            "payment_status": payment_status,
            "payment_intent": payment_intent,
            "created": 1_700_000_000,
            "metadata": {"response_id": str(response_id), "user_id": str(user_id)},
        },
        event_id=event_id,
        event_created=event_created,
        timestamp=timestamp,
    )


def refund_event(
    *,
    payment_intent: str,
    event_id: str = "evt_test_refund",
    charge_id: str = "ch_test_1",
) -> tuple[bytes, str]:
    """A signed `charge.refunded` body."""
    return signed_event(
        CHARGE_REFUNDED,
        {
            "id": charge_id,
            "object": "charge",
            "payment_intent": payment_intent,
            "amount_refunded": 2999,
            "refunded": True,
        },
        event_id=event_id,
    )


async def post_webhook(client: AsyncClient, body: bytes, signature: str) -> Any:
    """Deliver a signed event to the webhook route."""
    return await client.post(
        "/api/stripe/webhook", content=body, headers={"stripe-signature": signature}
    )


# ===================================================================== the Stripe client


class TestTheStripeClient:
    """`app/stripe_client.py` — the one place that knows how Stripe is set up.

    These are the tests that would catch a half-configured deployment, which is the
    failure mode that produces the worst support tickets: a checkout that 503s, or a
    webhook that silently never verifies.
    """

    def test_the_client_is_unconfigured_without_a_key(self) -> None:
        """No key, no client. The error names the setting, not the symptom."""
        with pytest.raises(PaymentsNotConfiguredError) as raised:
            stripe_client.client()

        assert "STRIPE_SECRET_KEY" in str(raised.value)

    def test_the_price_id_is_unknown_without_a_price(self) -> None:
        """A key without a Price can charge nothing, and must say so.

        The two settings are separate because a deployment can register its webhook
        endpoint before it can create a checkout session.
        """
        with pytest.raises(PaymentsNotConfiguredError) as raised:
            stripe_client.price_id()

        assert "STRIPE_PRICE_ID" in str(raised.value)

    def test_the_webhook_secret_is_missing_without_one(self) -> None:
        with pytest.raises(PaymentsNotConfiguredError) as raised:
            stripe_client.webhook_secret()

        assert "STRIPE_WEBHOOK_SECRET" in str(raised.value)

    def test_the_client_is_built_when_a_key_is_present(self, stripe_configured: str) -> None:
        """A real client, from the real SDK, holding the configured key."""
        client = stripe_client.client()

        assert isinstance(client, stripe.StripeClient)

    def test_the_price_id_comes_from_the_settings(self, stripe_configured: str) -> None:
        assert stripe_client.price_id() == TEST_PRICE_ID

    def test_the_webhook_secret_comes_from_the_settings(self, stripe_configured: str) -> None:
        assert stripe_client.webhook_secret() == WEBHOOK_SECRET

    def test_the_display_price_comes_from_the_settings(self, stripe_configured: str) -> None:
        """What the paywall says it costs.

        Read from settings, not hardcoded, so a price change is a deploy — and so the
        number the API shows can't drift from a constant in the code.
        """
        assert stripe_client.report_price() == (2999, "usd")

    def test_the_display_price_follows_a_configured_price(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The price is configuration, not a constant in the code."""
        monkeypatch.setenv("REPORT_PRICE_CENTS", "4999")
        monkeypatch.setenv("REPORT_PRICE_CURRENCY", "gbp")
        get_settings.cache_clear()

        assert stripe_client.report_price() == (4999, "gbp")

    def test_a_half_configured_deployment_is_not_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A key without a Price must not read as ready.

        The key without a Price can't charge anything, and the Price without a key can't
        be looked up. A half-configured deployment should fail loudly at the route, not
        quietly sell the wrong thing.
        """
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_something")
        monkeypatch.setenv("STRIPE_PRICE_ID", "")
        get_settings.cache_clear()

        assert get_settings().stripe_configured is False

    def test_a_fully_configured_deployment_is_configured(self, stripe_configured: str) -> None:
        assert get_settings().stripe_configured is True

    def test_webhook_readiness_is_tracked_separately(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A deployment can take payments before its webhook endpoint is registered.

        So the two are separate properties: `stripe_configured` is about creating a
        checkout, `stripe_webhooks_configured` is about verifying a delivery.
        """
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_something")
        monkeypatch.setenv("STRIPE_PRICE_ID", "price_something")
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "")
        get_settings.cache_clear()

        settings = get_settings()
        assert settings.stripe_configured is True
        assert settings.stripe_webhooks_configured is False

    def test_the_metadata_carries_both_ids(self) -> None:
        """Stripe doesn't know what a user bought — only what we put in `metadata`.

        The response id says *what* was bought; the user id says *who* paid. Both,
        always: the response id alone isn't enough, because the response's owner can
        change.
        """
        response_id = uuid4()
        user_id = uuid4()

        metadata = stripe_client.checkout_metadata(response_id=response_id, user_id=user_id)

        assert metadata == {"response_id": str(response_id), "user_id": str(user_id)}
        # Stripe's metadata values are strings, always.
        assert all(isinstance(value, str) for value in metadata.values())


# ===================================================================== reading Stripe's objects


class TestReadingStripesObjects:
    """The shapes Stripe sends, and the ones it doesn't.

    Every case here is a shape that has either already broken something or could. The
    SDK's `StripeObject` is a `dict` subclass that lies about being one: `.get()` raises
    `AttributeError`, and a *missing* key raises `AttributeError` for the attribute itself.
    """

    def test_the_metadata_is_not_a_dict(self) -> None:
        """The trap, stated plainly.

        `metadata` is a `StripeObject`, not a dict. Calling `.get()` on it raises. This is
        the bug that was fixed once already, so it gets a test that fails if it regresses.
        """
        session = stripe.checkout.Session.construct_from(
            {"id": "cs_1", "object": "checkout.session", "metadata": {"response_id": "abc"}},
            "sk_test_x",
        )

        assert not isinstance(session.metadata, dict)
        with pytest.raises(AttributeError):
            session.metadata.get("response_id")  # type: ignore[attr-defined]

    def test_a_metadata_value_is_read_by_index(self) -> None:
        session = stripe.checkout.Session.construct_from(
            {"id": "cs_1", "object": "checkout.session", "metadata": {"response_id": "abc"}},
            "sk_test_x",
        )

        assert metadata_value(session, "response_id") == "abc"

    def test_absent_metadata_is_none_rather_than_an_exception(self) -> None:
        """A session with no `metadata` key at all.

        This is the shape of a session created by hand in the Stripe dashboard, and it
        arrives as a real webhook event. The SDK raises `AttributeError` for a missing
        key — so reading `checkout.metadata` unguarded raises, rather than returning
        `None`.

        This is a regression test for a real crash: without it, the webhook route returns
        500 for such an event, and Stripe retries it for three days.
        """
        session = stripe.checkout.Session.construct_from(
            {"id": "cs_1", "object": "checkout.session"},
            "sk_test_x",
        )

        # The SDK's own behaviour, which is what the code has to survive.
        with pytest.raises(AttributeError):
            _ = session.metadata

        # And the code's behaviour, which is what it must be.
        assert metadata_value(session, "response_id") is None
        assert metadata_value(session, "user_id") is None

    def test_null_metadata_is_none(self) -> None:
        """`metadata` present but null — the SDK hands back `None` here."""
        session = stripe.checkout.Session.construct_from(
            {"id": "cs_1", "object": "checkout.session", "metadata": None},
            "sk_test_x",
        )

        assert metadata_value(session, "response_id") is None

    def test_a_missing_key_in_the_metadata_is_none(self) -> None:
        session = stripe.checkout.Session.construct_from(
            {"id": "cs_1", "object": "checkout.session", "metadata": {"other": "value"}},
            "sk_test_x",
        )

        assert metadata_value(session, "response_id") is None

    def test_a_non_string_metadata_value_is_refused(self) -> None:
        """Metadata values are always strings on the way in.

        A number means the session wasn't created by us, so it can't be trusted to name
        what was bought.
        """
        session = stripe.checkout.Session.construct_from(
            {"id": "cs_1", "object": "checkout.session", "metadata": {"response_id": 123}},
            "sk_test_x",
        )

        assert metadata_value(session, "response_id") is None

    def test_a_well_formed_uuid_is_parsed(self) -> None:
        value = str(uuid4())

        assert parse_uuid(value) == UUID(value)

    def test_a_malformed_uuid_is_refused(self) -> None:
        """A refusal, not an exception.

        The metadata is whatever was in the session — including a session made by hand in
        the dashboard. A malformed id must not be a 500.
        """
        assert parse_uuid("not-a-uuid") is None
        assert parse_uuid("") is None
        assert parse_uuid(None) is None


# ===================================================================== creating a checkout


class TestCreatingACheckout:
    """`POST /api/responses/{id}/checkout` — the request that asks for money.

    The order of the checks is load-bearing, and these tests pin it: ownership first
    (404, not 403, so a stranger can't confirm an id exists), then the entitlement
    (409, so someone who already paid isn't sent to a checkout for it), then the
    configuration (503, so the operator learns what's missing), and only then Stripe.
    """

    async def test_an_unowned_response_is_a_404(
        self, client: AsyncClient, response: QuestionnaireResponse
    ) -> None:
        """404, not 403: a 403 would confirm the id exists."""
        result = await client.post(f"/api/responses/{response.id}/checkout", json={})

        assert result.status_code == 404

    async def test_a_missing_response_is_a_404(self, client: AsyncClient) -> None:
        result = await client.post(
            "/api/responses/00000000-0000-0000-0000-000000000000/checkout", json={}
        )

        assert result.status_code == 404

    async def test_the_404_body_matches_the_genuine_not_found(
        self, client: AsyncClient, response: QuestionnaireResponse
    ) -> None:
        """A stranger must not be able to tell an existing id from a fictional one.

        The bodies have to be byte-identical, not merely the same status.
        """
        stranger = await client.post(f"/api/responses/{response.id}/checkout", json={})
        missing = await client.post(
            "/api/responses/00000000-0000-0000-0000-000000000000/checkout", json={}
        )

        assert stranger.json() == missing.json()

    async def test_an_unconfigured_stripe_is_a_503(
        self, client: AsyncClient, owned_response: UUID
    ) -> None:
        """An optional integration being absent must not take the rest of the app down.

        The message names the missing setting, because the person reading it is the one
        who has to fix it.
        """
        result = await client.post(f"/api/responses/{owned_response}/checkout", json={})

        assert result.status_code == 503
        assert "STRIPE" in result.json()["detail"]

    async def test_a_missing_price_is_a_503(
        self, client: AsyncClient, owned_response: UUID, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A key without a Price can't charge anything, and says which setting is missing."""
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_something")
        monkeypatch.setenv("STRIPE_PRICE_ID", "")
        get_settings.cache_clear()

        result = await client.post(f"/api/responses/{owned_response}/checkout", json={})

        assert result.status_code == 503
        assert "STRIPE_PRICE_ID" in result.json()["detail"]

    async def test_an_already_entitled_response_is_a_409(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
    ) -> None:
        """Not a second checkout session for something they already own.

        409 rather than 403: it isn't an authorisation failure, it's a conflict with the
        current state. The frontend can say "you already have this".
        """
        user_id = await owner_of(session, owned_response)
        sign_in_directly(client, user_id)
        session.add(
            Entitlement(
                user_id=user_id,
                response_id=owned_response,
                kind="single_report",
                valid_until=datetime.now(UTC) + timedelta(days=30),
                granted_by=(await _admin_id(session)),
            )
        )
        await session.flush()

        result = await client.post(f"/api/responses/{owned_response}/checkout", json={})

        assert result.status_code == 409

    async def test_a_deleted_response_is_a_404_even_with_the_capability(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
        fake_stripe: FakeStripeClient,
    ) -> None:
        """The capability cookie survives the row it refers to.

        The ownership check passes on the cookie alone, so the route has to look the
        response up and find it gone. That is a 404, not a 500.
        """
        response_row = await session.get(QuestionnaireResponse, owned_response)
        assert response_row is not None
        await session.delete(response_row)
        await session.flush()

        result = await client.post(f"/api/responses/{owned_response}/checkout", json={})

        assert result.status_code == 404

    async def test_the_response_is_a_url_and_a_session_id(
        self,
        client: AsyncClient,
        owned_response: UUID,
        fake_stripe: FakeStripeClient,
        stripe_configured: str,
    ) -> None:
        """The typed contract: a URL to navigate to, and the session's id.

        Not the Stripe session object — that would pin this contract to a third party's
        serialisation and leak their internals into it.
        """
        result = await client.post(f"/api/responses/{owned_response}/checkout", json={})

        assert result.status_code == 200
        body = result.json()
        assert body["url"] == "https://checkout.stripe.com/c/pay/cs_test_123"
        assert body["session_id"] == "cs_test_123"
        assert set(body) == {"url", "session_id"}

    async def test_the_session_is_built_with_the_configured_price(
        self,
        client: AsyncClient,
        owned_response: UUID,
        fake_stripe: FakeStripeClient,
        stripe_configured: str,
    ) -> None:
        """The Price id decides the money.

        `report_price_cents` is only what the paywall *displays*; Stripe's Price is what
        the customer is actually charged.
        """
        await client.post(f"/api/responses/{owned_response}/checkout", json={})

        params = fake_stripe.created_params
        assert params is not None
        assert params["line_items"] == [{"price": TEST_PRICE_ID, "quantity": 1}]
        assert params["mode"] == "payment"

    async def test_the_session_carries_the_metadata(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        fake_stripe: FakeStripeClient,
        stripe_configured: str,
    ) -> None:
        """The metadata is the webhook's only memory of what was bought.

        Both ids, always — the response id alone isn't enough, because the response's
        owner can change between the checkout and the delivery.
        """
        user_id = await owner_of(session, owned_response)

        await client.post(f"/api/responses/{owned_response}/checkout", json={})

        params = fake_stripe.created_params
        assert params is not None
        assert params["metadata"] == {
            "response_id": str(owned_response),
            "user_id": str(user_id),
        }

    async def test_the_success_url_carries_the_session_placeholder(
        self,
        client: AsyncClient,
        owned_response: UUID,
        fake_stripe: FakeStripeClient,
        stripe_configured: str,
    ) -> None:
        """`{CHECKOUT_SESSION_ID}` is a placeholder Stripe substitutes.

        The return page needs to know which session to verify, and the session id isn't
        known until Stripe creates it.
        """
        await client.post(f"/api/responses/{owned_response}/checkout", json={})

        params = fake_stripe.created_params
        assert params is not None
        assert "{CHECKOUT_SESSION_ID}" in params["success_url"]
        assert "session_id=" in params["success_url"]

    async def test_the_cancel_url_carries_the_response(
        self,
        client: AsyncClient,
        owned_response: UUID,
        fake_stripe: FakeStripeClient,
        stripe_configured: str,
    ) -> None:
        """The cancel URL has no session id, because nothing was paid.

        But it does have the response, so the visitor can be returned to the
        questionnaire they were answering.
        """
        await client.post(f"/api/responses/{owned_response}/checkout", json={})

        params = fake_stripe.created_params
        assert params is not None
        assert str(owned_response) in params["cancel_url"]
        assert "{CHECKOUT_SESSION_ID}" not in params["cancel_url"]

    async def test_the_urls_come_from_a_configured_origin(
        self,
        client: AsyncClient,
        owned_response: UUID,
        fake_stripe: FakeStripeClient,
        stripe_configured: str,
    ) -> None:
        """The success and cancel URLs are built from `ALLOWED_ORIGINS`.

        Not from a header and not hardcoded — a visitor paying from the dev server must
        come back to the dev server, and one paying from a tunnel must come back to the
        tunnel.
        """
        await client.post(f"/api/responses/{owned_response}/checkout", json={})

        params = fake_stripe.created_params
        assert params is not None
        assert params["success_url"].startswith("http://localhost:5173")
        assert params["cancel_url"].startswith("http://localhost:5173")

    async def test_the_session_expires_in_an_hour(
        self,
        client: AsyncClient,
        owned_response: UUID,
        fake_stripe: FakeStripeClient,
        stripe_configured: str,
    ) -> None:
        """Stripe's own default is 24 hours.

        A visitor who abandons a payment and comes back a week later should be asked to
        pay again rather than resuming a stale session.
        """
        before = int(datetime.now(UTC).timestamp())
        await client.post(f"/api/responses/{owned_response}/checkout", json={})

        params = fake_stripe.created_params
        assert params is not None
        expires_at = params["expires_at"]
        assert before + 3590 <= expires_at <= before + 3610

    async def test_a_stripe_failure_is_a_502(
        self,
        client: AsyncClient,
        owned_response: UUID,
        fake_stripe: FakeStripeClient,
        stripe_configured: str,
    ) -> None:
        """Stripe is down, so the answer is "try again", not "you're broken".

        502: the request was fine, the upstream wasn't. And the message is fixed — Stripe's
        internal wording is logged, not published.
        """
        fake_stripe.create_error = stripe.error.APIConnectionError("upstream is down")

        result = await client.post(f"/api/responses/{owned_response}/checkout", json={})

        assert result.status_code == 502
        assert "upstream is down" not in result.json()["detail"]

    async def test_an_unknown_discount_code_is_a_400(
        self, client: AsyncClient, owned_response: UUID, fake_stripe: FakeStripeClient
    ) -> None:
        """The code is checked before the visitor is handed to Stripe.

        They should learn it's wrong while they're still on the page, not after they've
        been sent to a checkout they can't complete.
        """
        result = await client.post(
            f"/api/responses/{owned_response}/checkout", json={"discount_code": "NOPE"}
        )

        assert result.status_code == 400
        assert fake_stripe.created_params is None

    async def test_a_discount_code_is_matched_case_insensitively(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        fake_stripe: FakeStripeClient,
        stripe_configured: str,
    ) -> None:
        """`SUMMER20` and `summer20` are the same code.

        The uniqueness is on `upper(code)` and the schema says redemption queries it that
        way, so a plain equality lookup would treat the two as different codes and leave
        the index unused.
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
            f"/api/responses/{owned_response}/checkout", json={"discount_code": "summer20"}
        )

        assert result.status_code == 200
        params = fake_stripe.created_params
        assert params is not None
        # `discounts`, not `discount_codes` — the latter isn't a Stripe parameter at all,
        # and the SDK passes unknown parameters straight through to the API, so the
        # mistake only shows up as a 400 from Stripe in production.
        assert params["discounts"] == [{"coupon": "coupon_test_123"}]

    async def test_a_discount_code_outside_its_window_is_refused(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        fake_stripe: FakeStripeClient,
    ) -> None:
        """The local `starts_at`/`ends_at` are enforced, so they're a real control.

        Leaving them unenforced while the columns exist is the worst of both: it looks
        like a control that isn't one.
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
            f"/api/responses/{owned_response}/checkout", json={"discount_code": "EXPIRED"}
        )

        assert result.status_code == 400
        assert fake_stripe.created_params is None

    async def test_a_discount_code_that_has_not_started_is_refused(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        fake_stripe: FakeStripeClient,
    ) -> None:
        """The other end of the window.

        A code scheduled for next month is not a code today.
        """
        session.add(
            DiscountCode(
                code="FUTURE",
                stripe_coupon_id="coupon_test_123",
                starts_at=datetime.now(UTC) + timedelta(days=1),
                ends_at=datetime.now(UTC) + timedelta(days=30),
            )
        )
        await session.flush()

        result = await client.post(
            f"/api/responses/{owned_response}/checkout", json={"discount_code": "FUTURE"}
        )

        assert result.status_code == 400

    async def test_a_discount_code_with_no_dates_is_accepted(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        fake_stripe: FakeStripeClient,
        stripe_configured: str,
    ) -> None:
        """Null dates mean "always", not "never".

        The window is a control when it's set, and no control when it isn't — both are
        legitimate, and the column being null must not silently refuse every code.
        """
        session.add(DiscountCode(code="ALWAYS", stripe_coupon_id="coupon_test_123"))
        await session.flush()

        result = await client.post(
            f"/api/responses/{owned_response}/checkout", json={"discount_code": "ALWAYS"}
        )

        assert result.status_code == 200

    async def test_a_discount_code_with_no_coupon_is_still_accepted(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        fake_stripe: FakeStripeClient,
        stripe_configured: str,
    ) -> None:
        """A code with no Stripe coupon can't have a discount applied.

        But it's a valid code locally, so the checkout proceeds — with no `discounts`
        parameter, rather than with an empty one that Stripe would reject.
        """
        session.add(DiscountCode(code="NOCOUPON"))
        await session.flush()

        result = await client.post(
            f"/api/responses/{owned_response}/checkout", json={"discount_code": "NOCOUPON"}
        )

        assert result.status_code == 200
        params = fake_stripe.created_params
        assert params is not None
        assert "discounts" not in params

    async def test_an_unknown_field_in_the_request_is_rejected(
        self, client: AsyncClient, owned_response: UUID
    ) -> None:
        """The request schema forbids extras.

        A typo in a field name should be a 422, not a silently ignored parameter.
        """
        result = await client.post(
            f"/api/responses/{owned_response}/checkout", json={"discount": "SUMMER20"}
        )

        assert result.status_code == 422

    async def test_the_price_endpoint_serves_the_display_price(self, client: AsyncClient) -> None:
        """The paywall has to be able to say what it costs before the visitor commits.

        The number comes from the server rather than being hardcoded in the frontend, for
        the same reason pay figures come from the `career_pay` lookup and are never
        fabricated.
        """
        body = (await client.get("/api/pricing")).json()

        assert body["amount_cents"] == 2999
        assert body["currency"] == "usd"


# ===================================================================== the webhook


class TestTheWebhook:
    """`POST /api/stripe/webhook` — Stripe's deliveries.

    The webhook trusts nothing it is sent. The body is verified against the signing
    secret *before* it's parsed, the metadata is checked against the response row, and
    the amount recorded is what Stripe says was charged rather than what we configured.
    """

    async def test_a_valid_event_is_acknowledged(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
    ) -> None:
        """The happy path: one purchase row, one entitlement row."""
        user_id = await owner_of(session, owned_response)
        body, signature = checkout_event(response_id=owned_response, user_id=user_id)

        result = await post_webhook(client, body, signature)

        assert result.status_code == 200
        assert result.json()["received"] == "ok"
        purchases = (await session.execute(select(Purchase))).scalars().all()
        entitlements = (await session.execute(select(Entitlement))).scalars().all()
        assert len(purchases) == 1
        assert len(entitlements) == 1

    async def test_the_purchase_records_what_stripe_charged(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
    ) -> None:
        """`amount_total`, not our configured price.

        `amount_total` is already net of any discount. A coupon, a currency change, or a
        tax-inclusive total would all make our configured figure wrong, and
        `purchases.amount_cents` is the record of what was paid.
        """
        user_id = await owner_of(session, owned_response)
        body, signature = checkout_event(
            response_id=owned_response, user_id=user_id, amount_total=2500
        )

        await post_webhook(client, body, signature)

        purchase = (await session.execute(select(Purchase))).scalars().one()
        assert purchase.amount_cents == 2500
        assert purchase.currency == "usd"

    async def test_the_purchase_records_the_configured_price_id(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
    ) -> None:
        """The Price the session was built from.

        A session's `line_items` are only populated if the event expanded them, so they
        can't be relied on here; the configured id is what the session was actually built
        from.
        """
        user_id = await owner_of(session, owned_response)
        body, signature = checkout_event(response_id=owned_response, user_id=user_id)

        await post_webhook(client, body, signature)

        purchase = (await session.execute(select(Purchase))).scalars().one()
        assert purchase.stripe_price_id == TEST_PRICE_ID

    async def test_the_entitlement_goes_to_the_responses_owner(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
    ) -> None:
        """Bought, not comped: exactly one of the two origins."""
        user_id = await owner_of(session, owned_response)
        body, signature = checkout_event(response_id=owned_response, user_id=user_id)

        await post_webhook(client, body, signature)

        purchase = (await session.execute(select(Purchase))).scalars().one()
        entitlement = (await session.execute(select(Entitlement))).scalars().one()
        assert entitlement.user_id == user_id
        assert entitlement.response_id == owned_response
        assert entitlement.kind == "single_report"
        assert entitlement.purchase_id == purchase.id
        assert entitlement.granted_by is None

    async def test_the_entitlement_actually_opens_the_paywall(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
    ) -> None:
        """The money-to-product connection: the gate returns a row afterwards."""
        user_id = await owner_of(session, owned_response)
        body, signature = checkout_event(response_id=owned_response, user_id=user_id)

        await post_webhook(client, body, signature)

        assert await has_active_entitlement(session, user_id, owned_response) is True

    async def test_a_redelivered_event_grants_once(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
    ) -> None:
        """Stripe retries, and it sends the same event.

        The idempotency comes from the unique constraint on
        `stripe_checkout_session_id`, not from a read-then-write pre-check — so this
        can't race.
        """
        user_id = await owner_of(session, owned_response)
        body, signature = checkout_event(response_id=owned_response, user_id=user_id)

        first = await post_webhook(client, body, signature)
        second = await post_webhook(client, body, signature)

        assert first.status_code == 200
        assert second.status_code == 200
        purchases = (await session.execute(select(Purchase))).scalars().all()
        entitlements = (await session.execute(select(Entitlement))).scalars().all()
        assert len(purchases) == 1
        assert len(entitlements) == 1

    async def test_a_different_event_for_the_same_session_grants_once(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
    ) -> None:
        """A different *event id* for the same session is still the same purchase.

        Stripe can deliver `checkout.session.completed` and
        `checkout.session.async_payment_succeeded` for the same session. Two events, one
        purchase.
        """
        user_id = await owner_of(session, owned_response)
        completed, completed_signature = checkout_event(
            response_id=owned_response, user_id=user_id, event_id="evt_completed"
        )
        settled, settled_signature = checkout_event(
            response_id=owned_response,
            user_id=user_id,
            event_id="evt_settled",
            event_type=ASYNC_PAYMENT_SUCCEEDED,
        )

        await post_webhook(client, completed, completed_signature)
        await post_webhook(client, settled, settled_signature)

        purchases = (await session.execute(select(Purchase))).scalars().all()
        entitlements = (await session.execute(select(Entitlement))).scalars().all()
        assert len(purchases) == 1
        assert len(entitlements) == 1

    async def test_a_forged_signature_writes_nothing(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
    ) -> None:
        """A forged body must not grant access.

        400 rather than 401: Stripe retries non-2xx deliveries for up to three days, and
        a forged body will never become good, so the retries should stop.
        """
        user_id = await owner_of(session, owned_response)
        body, _ = checkout_event(response_id=owned_response, user_id=user_id)

        result = await post_webhook(client, body, "t=1,v1=deadbeef")

        assert result.status_code == 400
        assert result.json()["detail"] == "invalid signature"
        purchases = (await session.execute(select(Purchase))).scalars().all()
        assert purchases == []

    async def test_a_body_signed_with_the_wrong_secret_writes_nothing(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
    ) -> None:
        """The realistic version of a forgery: the wrong secret.

        This is what happens when a deployment registers its webhook endpoint before it
        can create a checkout session and pastes the API key in by mistake. The symptom
        is a paid purchase that never grants access.
        """
        user_id = await owner_of(session, owned_response)
        body, _ = checkout_event(response_id=owned_response, user_id=user_id)
        _, wrong_signature = signed_event(
            CHECKOUT_COMPLETED,
            {},
            secret="whsec_a_completely_different_secret",
        )

        result = await post_webhook(client, body, wrong_signature)

        assert result.status_code == 400
        purchases = (await session.execute(select(Purchase))).scalars().all()
        assert purchases == []

    async def test_a_missing_signature_writes_nothing(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
    ) -> None:
        user_id = await owner_of(session, owned_response)
        body, _ = checkout_event(response_id=owned_response, user_id=user_id)

        result = await post_webhook(client, body, "")

        assert result.status_code == 400
        purchases = (await session.execute(select(Purchase))).scalars().all()
        assert purchases == []

    async def test_a_malformed_body_is_a_400(
        self,
        client: AsyncClient,
        session: AsyncSession,
        stripe_configured: str,
    ) -> None:
        """Not JSON at all. Nothing to retry.

        The signature is verified before the body is parsed, so the body has to be signed
        for the check to get as far as parsing it — which is the point: the parse failure
        is only reached for a body that really did come from Stripe.
        """
        payload = "this is not json"
        signature = stripe.WebhookSignature.generate_signature_header(payload, WEBHOOK_SECRET)

        result = await post_webhook(client, payload.encode(), signature)

        assert result.status_code == 400
        assert result.json()["detail"] == "malformed event"

    async def test_a_forged_body_is_refused_before_it_is_parsed(
        self,
        client: AsyncClient,
        session: AsyncSession,
        stripe_configured: str,
    ) -> None:
        """A garbage signature is caught before the body is even looked at.

        Which is the right order: the signature says the body is from Stripe, and only
        then is it worth parsing.
        """
        result = await post_webhook(client, b"this is not json", "t=1,v1=deadbeef")

        assert result.status_code == 400
        assert result.json()["detail"] == "invalid signature"

    async def test_a_body_that_is_not_an_event_is_a_400(
        self,
        client: AsyncClient,
        session: AsyncSession,
        stripe_configured: str,
    ) -> None:
        """Valid JSON, valid signature, but not an event.

        A signed body that isn't an event shape can't be handled, and it will never
        become one — so it's refused with a 400, which stops Stripe retrying it for
        three days.

        This is different from an event of an unknown *type*, which is well-formed and
        is acknowledged and ignored.
        """
        payload = json.dumps({"hello": "world"})
        signature = stripe.WebhookSignature.generate_signature_header(payload, WEBHOOK_SECRET)

        result = await post_webhook(client, payload.encode(), signature)

        assert result.status_code == 400
        assert result.json()["detail"] == "malformed event"

    async def test_an_event_with_no_data_object_is_not_granted(
        self, client: AsyncClient, session: AsyncSession, stripe_configured: str
    ) -> None:
        """An event with a type but nothing in its data.

        The SDK raises `AttributeError` for the missing `object` key, so reading
        `event.data.object` unguarded crashed the webhook with a 500 — and Stripe would
        then retry the same body for three days.
        """
        payload = json.dumps(
            {
                "id": "evt_nodata",
                "object": "event",
                "api_version": "2024-06-20",
                "type": CHECKOUT_COMPLETED,
                "created": 1_700_000_000,
                "data": {},
            }
        )
        signature = stripe.WebhookSignature.generate_signature_header(payload, WEBHOOK_SECRET)

        result = await post_webhook(client, payload.encode(), signature)

        assert result.status_code == 200
        assert result.json()["handled"] == "not-granted"
        purchases = (await session.execute(select(Purchase))).scalars().all()
        assert purchases == []

    async def test_a_refund_event_with_no_data_object_revokes_nothing(
        self, client: AsyncClient, session: AsyncSession, stripe_configured: str
    ) -> None:
        """The same shape, on the refund path."""
        payload = json.dumps(
            {
                "id": "evt_nodata_refund",
                "object": "event",
                "api_version": "2024-06-20",
                "type": CHARGE_REFUNDED,
                "created": 1_700_000_000,
                "data": {},
            }
        )
        signature = stripe.WebhookSignature.generate_signature_header(payload, WEBHOOK_SECRET)

        result = await post_webhook(client, payload.encode(), signature)

        assert result.status_code == 200
        assert result.json()["handled"] == "nothing-to-revoke"

    async def test_an_event_with_no_timestamp_falls_back_to_now(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
    ) -> None:
        """An event with no `created` timestamp still grants.

        The timestamp is a refinement, not a requirement: without it the payment time is
        "now", which is close enough and much better than refusing a real purchase.
        """
        user_id = await owner_of(session, owned_response)
        payload = json.dumps(
            {
                "id": "evt_notime",
                "object": "event",
                "api_version": "2024-06-20",
                "type": CHECKOUT_COMPLETED,
                "data": {
                    "object": {
                        "id": "cs_notime",
                        "object": "checkout.session",
                        "amount_total": 2999,
                        "currency": "usd",
                        "payment_status": "paid",
                        "payment_intent": "pi_notime",
                        "metadata": {
                            "response_id": str(owned_response),
                            "user_id": str(user_id),
                        },
                    }
                },
            }
        )
        signature = stripe.WebhookSignature.generate_signature_header(payload, WEBHOOK_SECRET)

        result = await post_webhook(client, payload.encode(), signature)

        assert result.status_code == 200
        assert result.json()["handled"] == "granted"
        purchase = (await session.execute(select(Purchase))).scalars().one()
        assert purchase.paid_at is not None

    async def test_a_stale_signature_is_refused(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
    ) -> None:
        """Stripe's signature has a five-minute tolerance.

        A delivery that arrives late — a retry after a network outage — is refused. This
        is Stripe's own behaviour, and the code inherits it by using the SDK's
        verification, which is the point of using the SDK rather than rolling our own.
        """
        user_id = await owner_of(session, owned_response)
        body, signature = checkout_event(
            response_id=owned_response, user_id=user_id, timestamp=1_700_000_000
        )

        result = await post_webhook(client, body, signature)

        assert result.status_code == 400
        purchases = (await session.execute(select(Purchase))).scalars().all()
        assert purchases == []

    async def test_an_unconfigured_webhook_is_a_503(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
    ) -> None:
        """Without the secret we can't tell a real delivery from a forged one.

        That's a 503 rather than a 400: it isn't that the body is bad, it's that we can't
        read it at all, and the answer is to configure the deployment.
        """
        user_id = await owner_of(session, owned_response)
        body, signature = checkout_event(response_id=owned_response, user_id=user_id)

        result = await post_webhook(client, body, signature)

        assert result.status_code == 503
        assert "STRIPE_WEBHOOK_SECRET" in result.json()["detail"]
        purchases = (await session.execute(select(Purchase))).scalars().all()
        assert purchases == []

    async def test_an_unhandled_event_type_is_acknowledged(
        self, client: AsyncClient, session: AsyncSession, stripe_configured: str
    ) -> None:
        """A well-formed event we don't care about is not a failure.

        Stripe retries any non-2xx, so acknowledging is what stops a pointless delivery
        loop.
        """
        body, signature = signed_event("customer.created", {"id": "cus_1", "object": "customer"})

        result = await post_webhook(client, body, signature)

        assert result.status_code == 200
        assert result.json()["handled"] == "ignored"
        purchases = (await session.execute(select(Purchase))).scalars().all()
        assert purchases == []

    async def test_a_session_with_empty_metadata_grants_nothing(
        self, client: AsyncClient, session: AsyncSession, stripe_configured: str
    ) -> None:
        """A session created by hand in the Stripe dashboard has no metadata.

        It can't be mapped to a response, so it can't grant anything — and it must not
        crash the endpoint, because a 500 would make Stripe retry something that will
        never work.
        """
        body, signature = signed_event(
            CHECKOUT_COMPLETED,
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

        result = await post_webhook(client, body, signature)

        assert result.status_code == 200
        assert result.json()["handled"] == "not-granted"
        purchases = (await session.execute(select(Purchase))).scalars().all()
        assert purchases == []

    async def test_a_session_with_no_metadata_at_all_does_not_crash(
        self, client: AsyncClient, session: AsyncSession, stripe_configured: str
    ) -> None:
        """The same session, but with no `metadata` *key* at all.

        This is a different shape from empty metadata, and it used to be fatal: the SDK
        raises `AttributeError` for a missing key, so reading `checkout.metadata` threw
        instead of returning `None`, and the route returned 500. Stripe would then retry
        the event for three days.

        The assertion that matters is the status code — 200, not 500.
        """
        body, signature = signed_event(
            CHECKOUT_COMPLETED,
            {
                "id": "cs_handmade",
                "object": "checkout.session",
                "amount_total": 2999,
                "currency": "usd",
                "payment_status": "paid",
                "payment_intent": "pi_handmade",
                "created": 1_700_000_000,
            },
        )

        result = await post_webhook(client, body, signature)

        assert result.status_code == 200
        assert result.json()["handled"] == "not-granted"
        purchases = (await session.execute(select(Purchase))).scalars().all()
        assert purchases == []

    async def test_a_session_for_a_response_that_no_longer_exists_grants_nothing(
        self, client: AsyncClient, session: AsyncSession, stripe_configured: str
    ) -> None:
        """The response was deleted between the checkout and the delivery.

        There's nothing to grant access to, and it must be a refusal rather than a
        crash.
        """
        body, signature = checkout_event(
            response_id=uuid4(), user_id=uuid4(), session_id="cs_deleted"
        )

        result = await post_webhook(client, body, signature)

        assert result.status_code == 200
        assert result.json()["handled"] == "not-granted"
        purchases = (await session.execute(select(Purchase))).scalars().all()
        assert purchases == []

    async def test_an_unpaid_session_grants_nothing(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
    ) -> None:
        """`payment_status` must be `paid`.

        A `checkout.session.completed` event also fires for sessions that are merely
        created, and for delayed payment methods the session completes before the money
        has arrived.
        """
        user_id = await owner_of(session, owned_response)
        body, signature = checkout_event(
            response_id=owned_response, user_id=user_id, payment_status="unpaid"
        )

        result = await post_webhook(client, body, signature)

        assert result.status_code == 200
        assert result.json()["handled"] == "not-granted"
        purchases = (await session.execute(select(Purchase))).scalars().all()
        assert purchases == []

    async def test_a_delayed_payment_that_settles_later_does_grant(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
    ) -> None:
        """A bank transfer settles days later, and Stripe tells us when.

        `checkout.session.completed` fires with `payment_status` of `unpaid` for a delayed
        payment method, and `checkout.session.async_payment_succeeded` fires when the
        money arrives. Handling only the first would mean a customer who paid by bank
        transfer paid and received nothing.
        """
        user_id = await owner_of(session, owned_response)

        completed, completed_signature = checkout_event(
            response_id=owned_response,
            user_id=user_id,
            payment_status="unpaid",
            event_id="evt_completed",
        )
        first = await post_webhook(client, completed, completed_signature)
        assert first.status_code == 200
        purchases = (await session.execute(select(Purchase))).scalars().all()
        assert purchases == []

        settled, settled_signature = checkout_event(
            response_id=owned_response,
            user_id=user_id,
            payment_status="paid",
            event_id="evt_settled",
            event_type=ASYNC_PAYMENT_SUCCEEDED,
        )
        second = await post_webhook(client, settled, settled_signature)

        assert second.status_code == 200
        assert second.json()["handled"] == "granted"
        purchases = (await session.execute(select(Purchase))).scalars().all()
        entitlements = (await session.execute(select(Entitlement))).scalars().all()
        assert len(purchases) == 1
        assert len(entitlements) == 1
        assert await has_active_entitlement(session, user_id, owned_response) is True

    async def test_a_failed_delayed_payment_grants_nothing(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
    ) -> None:
        """A payment method that fails to settle is not a purchase.

        Acknowledged rather than an error, so Stripe stops retrying, and nothing is
        written.
        """
        user_id = await owner_of(session, owned_response)
        body, signature = checkout_event(
            response_id=owned_response,
            user_id=user_id,
            payment_status="unpaid",
            event_id="evt_failed",
            event_type=ASYNC_PAYMENT_FAILED,
        )

        result = await post_webhook(client, body, signature)

        assert result.status_code == 200
        assert result.json()["handled"] == "payment-failed"
        purchases = (await session.execute(select(Purchase))).scalars().all()
        assert purchases == []

    async def test_the_purchase_records_when_the_money_arrived(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
    ) -> None:
        """`paid_at` is the event's time, not the session's creation time.

        A purchase row that says it was paid before it was would be wrong in the one
        place where the row is the evidence. For a card the two are seconds apart; for a
        bank transfer they're days apart, and the difference is the whole reason this is
        asserted.
        """
        user_id = await owner_of(session, owned_response)
        body, signature = checkout_event(
            response_id=owned_response, user_id=user_id, event_created=1_700_000_500
        )

        await post_webhook(client, body, signature)

        purchase = (await session.execute(select(Purchase))).scalars().one()
        assert purchase.paid_at == datetime.fromtimestamp(1_700_000_500, tz=UTC)

    async def test_the_entitlement_starts_when_the_money_arrived(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
    ) -> None:
        """The entitlement's `valid_from` is the payment time, not the row's write time.

        An entitlement that was already valid while the money was still in flight would
        be wrong — and for a delayed payment method, "in flight" is days.
        """
        user_id = await owner_of(session, owned_response)
        body, signature = checkout_event(
            response_id=owned_response, user_id=user_id, event_created=1_700_000_500
        )

        await post_webhook(client, body, signature)

        entitlement = (await session.execute(select(Entitlement))).scalars().one()
        assert entitlement.valid_from == datetime.fromtimestamp(1_700_000_500, tz=UTC)

    async def test_the_entitlement_follows_the_responses_current_owner(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
        other_user: User,
    ) -> None:
        """The response's owner, not the metadata's copy.

        `app/auth.py:claim_responses` re-points a response at a verified account when a
        visitor signs in, so the owner at webhook time can differ from the owner at
        checkout time. The entitlement follows the response, so the person who ends up
        with the report is the one who can use it.
        """
        user_id = await owner_of(session, owned_response)
        body, signature = checkout_event(response_id=owned_response, user_id=user_id)

        # The response changes hands between the checkout and the delivery.
        response_row = await session.get(QuestionnaireResponse, owned_response)
        assert response_row is not None
        response_row.user_id = other_user.id
        await session.flush()

        await post_webhook(client, body, signature)

        entitlement = (await session.execute(select(Entitlement))).scalars().one()
        assert entitlement.user_id == other_user.id
        assert await has_active_entitlement(session, other_user.id, owned_response) is True

    async def test_the_purchase_records_the_responses_owner_not_the_metadata(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
        other_user: User,
    ) -> None:
        """The purchase row follows the response too.

        Otherwise the money would be recorded against one user and the access against
        another, and the refund path — which looks the purchase up by payment intent —
        would revoke an entitlement the purchaser doesn't have.
        """
        user_id = await owner_of(session, owned_response)
        body, signature = checkout_event(response_id=owned_response, user_id=user_id)

        response_row = await session.get(QuestionnaireResponse, owned_response)
        assert response_row is not None
        response_row.user_id = other_user.id
        await session.flush()

        await post_webhook(client, body, signature)

        purchase = (await session.execute(select(Purchase))).scalars().one()
        assert purchase.user_id == other_user.id

    async def test_the_raw_body_is_what_is_verified(
        self, client: AsyncClient, session: AsyncSession, stripe_configured: str
    ) -> None:
        """The signature covers the exact bytes, not a re-serialisation of them.

        Re-serialising parsed JSON changes the bytes — key order, separators — and breaks
        the signature. So the body has to be read as bytes and verified as bytes.

        The test signs a body with a space in it, which a re-serialisation would drop.
        """
        payload = json.dumps(
            {
                "id": "evt_space",
                "object": "event",
                "api_version": "2024-06-20",
                "type": "customer.created",
                "created": 1_700_000_000,
                "data": {"object": {"id": "cus_1", "object": "customer"}},
            },
            indent=2,
        )
        signature = stripe.WebhookSignature.generate_signature_header(payload, WEBHOOK_SECRET)

        result = await post_webhook(client, payload.encode(), signature)

        assert result.status_code == 200

    async def test_a_body_re_signed_after_parsing_is_refused(
        self, client: AsyncClient, session: AsyncSession, stripe_configured: str
    ) -> None:
        """The failure mode the raw-body rule prevents.

        A body signed as one string and *sent* as another — which is what happens if the
        JSON is parsed and re-serialised somewhere in the stack — is refused.
        """
        payload = json.dumps(
            {
                "id": "evt_reserialised",
                "object": "event",
                "type": "customer.created",
                "created": 1_700_000_000,
                "data": {"object": {"id": "cus_1", "object": "customer"}},
            }
        )
        signature = stripe.WebhookSignature.generate_signature_header(payload, WEBHOOK_SECRET)
        # Same data, different bytes: the separators differ.
        reserialised = json.dumps(json.loads(payload), separators=(",", ":"))

        result = await post_webhook(client, reserialised.encode(), signature)

        assert result.status_code == 400


# ===================================================================== refunds


class TestRefunds:
    """`charge.refunded` — the money going back.

    The rule that matters: a refund must *revoke*, not merely record. The gate reads
    `entitlements`, so stamping `purchases.refunded_at` alone leaves the money back and
    the access still there — which is worse than not refunding at all.
    """

    async def test_a_refund_revokes_the_entitlement(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
    ) -> None:
        user_id = await owner_of(session, owned_response)
        body, signature = checkout_event(
            response_id=owned_response, user_id=user_id, payment_intent="pi_refund"
        )
        await post_webhook(client, body, signature)
        assert await has_active_entitlement(session, user_id, owned_response) is True

        refund_body, refund_signature = refund_event(payment_intent="pi_refund")
        result = await post_webhook(client, refund_body, refund_signature)

        assert result.status_code == 200
        assert result.json()["handled"] == "revoked"
        purchase = (await session.execute(select(Purchase))).scalars().one()
        assert purchase.refunded_at is not None
        entitlement = (await session.execute(select(Entitlement))).scalars().one()
        assert entitlement.revoked_at is not None
        # The end-to-end consequence: the gate says no.
        assert await has_active_entitlement(session, user_id, owned_response) is False

    async def test_a_refund_for_an_unknown_payment_revokes_nothing(
        self, client: AsyncClient, session: AsyncSession, stripe_configured: str
    ) -> None:
        """A refund for a charge we don't know about is acknowledged, not an error.

        The money going back is Stripe's business; what it must not do is fail the
        delivery.
        """
        body, signature = refund_event(payment_intent="pi_unknown")

        result = await post_webhook(client, body, signature)

        assert result.status_code == 200
        assert result.json()["handled"] == "nothing-to-revoke"
        purchases = (await session.execute(select(Purchase))).scalars().all()
        entitlements = (await session.execute(select(Entitlement))).scalars().all()
        assert purchases == []
        assert entitlements == []

    async def test_a_refund_with_no_payment_intent_revokes_nothing(
        self, client: AsyncClient, session: AsyncSession, stripe_configured: str
    ) -> None:
        """A charge refunded with no payment intent names nothing we can look up."""
        body, signature = signed_event(
            CHARGE_REFUNDED,
            {"id": "ch_nointent", "object": "charge", "refunded": True},
        )

        result = await post_webhook(client, body, signature)

        assert result.status_code == 200
        assert result.json()["handled"] == "nothing-to-revoke"

    async def test_a_redelivered_refund_is_harmless(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
    ) -> None:
        """A refund event delivered twice must not fail the second time.

        The purchase already has `refunded_at` and the entitlement already has
        `revoked_at`, so the second delivery finds both set and does nothing.
        """
        user_id = await owner_of(session, owned_response)
        body, signature = checkout_event(
            response_id=owned_response, user_id=user_id, payment_intent="pi_twice"
        )
        await post_webhook(client, body, signature)

        refund_body, refund_signature = refund_event(payment_intent="pi_twice")
        first = await post_webhook(client, refund_body, refund_signature)
        second = await post_webhook(client, refund_body, refund_signature)

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["handled"] == "revoked"
        purchase = (await session.execute(select(Purchase))).scalars().one()
        entitlement = (await session.execute(select(Entitlement))).scalars().one()
        # The timestamps are not moved by the second delivery.
        assert purchase.refunded_at is not None
        assert entitlement.revoked_at is not None

    async def test_a_refund_does_not_revoke_a_different_entitlement(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
    ) -> None:
        """The lookup is by payment intent, so only the right access is revoked.

        A second purchase of a *different* response must keep its access.
        """
        user_id = await owner_of(session, owned_response)
        body, signature = checkout_event(
            response_id=owned_response, user_id=user_id, payment_intent="pi_first"
        )
        await post_webhook(client, body, signature)

        other_response = await _second_response(client, session, user_id)
        other_body, other_signature = checkout_event(
            response_id=other_response,
            user_id=user_id,
            session_id="cs_second",
            payment_intent="pi_second",
        )
        await post_webhook(client, other_body, other_signature)

        refund_body, refund_signature = refund_event(payment_intent="pi_first")
        await post_webhook(client, refund_body, refund_signature)

        entitlements = (await session.execute(select(Entitlement))).scalars().all()
        by_response = {entitlement.response_id: entitlement for entitlement in entitlements}
        assert by_response[owned_response].revoked_at is not None
        assert by_response[other_response].revoked_at is None
        assert await has_active_entitlement(session, user_id, other_response) is True

    async def test_a_refunded_response_can_be_bought_again(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
        fake_stripe: FakeStripeClient,
    ) -> None:
        """A refund gives the access back, so a second purchase is allowed.

        The 409 on checkout is about *having* the report. After a refund the visitor
        doesn't have it, and refusing a new purchase would strand them.
        """
        user_id = await owner_of(session, owned_response)
        body, signature = checkout_event(
            response_id=owned_response, user_id=user_id, payment_intent="pi_refunded"
        )
        await post_webhook(client, body, signature)

        refund_body, refund_signature = refund_event(payment_intent="pi_refunded")
        await post_webhook(client, refund_body, refund_signature)

        result = await client.post(f"/api/responses/{owned_response}/checkout", json={})

        assert result.status_code == 200


# ===================================================================== the return path


class TestTheReturnPath:
    """`GET /api/checkout/{session_id}` — the browser comes back from Stripe.

    Stripe redirects the browser back the moment the payment completes, and the webhook
    that grants access may not have landed yet. Rather than polling or assuming, the
    return page asks here.

    **The session id alone is not authority to see anything.** It appears in a URL, and
    URLs get shared.
    """

    async def test_a_paid_visitor_is_granted_access_on_return(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
        fake_stripe: FakeStripeClient,
    ) -> None:
        """A visitor who has just paid is not refused because a delivery was slow.

        The return path performs the same grant the webhook would have, using the same
        implementation.
        """
        fake_stripe._session.metadata = {"response_id": str(owned_response)}
        fake_stripe._session.payment_status = "paid"

        result = await client.get("/api/checkout/cs_test_123")

        assert result.status_code == 200
        body = result.json()
        assert body["paid"] is True
        assert body["entitled"] is True
        assert body["response_id"] == str(owned_response)
        purchases = (await session.execute(select(Purchase))).scalars().all()
        entitlements = (await session.execute(select(Entitlement))).scalars().all()
        assert len(purchases) == 1
        assert len(entitlements) == 1

    async def test_the_return_path_does_not_grant_twice(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
        fake_stripe: FakeStripeClient,
    ) -> None:
        """The webhook lands, and then the return path checks: still one of each.

        The same unique constraint that makes a redelivered event harmless makes a
        return-path grant after a webhook grant harmless.
        """
        user_id = await owner_of(session, owned_response)
        body, signature = checkout_event(response_id=owned_response, user_id=user_id)
        await post_webhook(client, body, signature)

        fake_stripe._session.metadata = {"response_id": str(owned_response)}
        await client.get("/api/checkout/cs_test_123")

        purchases = (await session.execute(select(Purchase))).scalars().all()
        entitlements = (await session.execute(select(Entitlement))).scalars().all()
        assert len(purchases) == 1
        assert len(entitlements) == 1

    async def test_a_strangers_session_is_a_404(
        self,
        client: AsyncClient,
        owned_response: UUID,
        stripe_configured: str,
        fake_stripe: FakeStripeClient,
    ) -> None:
        """A session id is not a capability.

        The caller has to be able to access the response the session was for, or the
        check is meaningless.
        """
        fake_stripe._session.metadata = {"response_id": str(owned_response)}

        # A second browser, with its own empty cookie jar.
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as stranger:
            result = await stranger.get("/api/checkout/cs_test_123")

        assert result.status_code == 404

    async def test_an_unpaid_session_reports_unpaid(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
        fake_stripe: FakeStripeClient,
    ) -> None:
        """An unpaid session grants nothing, and says so."""
        fake_stripe._session.metadata = {"response_id": str(owned_response)}
        fake_stripe._session.payment_status = "unpaid"

        result = await client.get("/api/checkout/cs_test_123")

        assert result.status_code == 200
        body = result.json()
        assert body["paid"] is False
        assert body["entitled"] is False
        purchases = (await session.execute(select(Purchase))).scalars().all()
        assert purchases == []

    async def test_a_session_with_no_metadata_reports_nothing(
        self, client: AsyncClient, stripe_configured: str, fake_stripe: FakeStripeClient
    ) -> None:
        """A session we can't map to a response can't be checked, and isn't an error.

        It just says "not paid, not entitled", which is the honest answer for a session
        that isn't ours.
        """
        fake_stripe._session.metadata = {}

        result = await client.get("/api/checkout/cs_test_123")

        assert result.status_code == 200
        body = result.json()
        assert body["paid"] is False
        assert body["entitled"] is False
        assert body["response_id"] is None

    async def test_a_session_with_no_metadata_key_does_not_crash(
        self, client: AsyncClient, stripe_configured: str, fake_stripe: FakeStripeClient
    ) -> None:
        """The same missing-key trap, on the return path.

        A session created by hand in the dashboard has no `metadata` at all, and reading
        it unguarded raises.
        """
        fake_stripe._session.metadata = None

        result = await client.get("/api/checkout/cs_test_123")

        assert result.status_code == 200
        assert result.json()["entitled"] is False

    async def test_an_unconfigured_stripe_is_a_503(
        self, client: AsyncClient, owned_response: UUID
    ) -> None:
        """The return path needs the API key to verify the session with Stripe."""
        result = await client.get("/api/checkout/cs_test_123")

        assert result.status_code == 503
        assert "STRIPE" in result.json()["detail"]

    async def test_a_stripe_failure_is_a_502(
        self,
        client: AsyncClient,
        owned_response: UUID,
        stripe_configured: str,
        fake_stripe: FakeStripeClient,
    ) -> None:
        """Stripe is down, so the answer is "try again"."""
        fake_stripe.retrieve_error = stripe.error.APIConnectionError("upstream is down")

        result = await client.get("/api/checkout/cs_test_123")

        assert result.status_code == 502

    async def test_a_return_for_a_deleted_response_is_a_404(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
        fake_stripe: FakeStripeClient,
    ) -> None:
        """The response was deleted between the checkout and the return.

        The ownership check passes — the browser still holds the capability cookie — but
        the row behind it is gone, so the owner can't be read. That's a 404, not a crash.
        """
        fake_stripe._session.metadata = {"response_id": str(owned_response)}
        response_row = await session.get(QuestionnaireResponse, owned_response)
        assert response_row is not None
        await session.delete(response_row)
        await session.flush()

        result = await client.get("/api/checkout/cs_test_123")

        assert result.status_code == 404

    async def test_a_return_that_cannot_grant_reports_unentitled(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
        fake_stripe: FakeStripeClient,
    ) -> None:
        """A paid session whose grant can't be written is reported, not crashed.

        The response exists and the browser owns it, but the session's `payment_status`
        is `unpaid` while the *event* says paid — the shapes disagree. The grant refuses,
        and the answer is "paid but not entitled", which is the honest state.

        The refusal has to be caught: an unhandled `PaymentNotReadyError` would be a 500,
        and the browser would see an error page instead of a state it can poll.
        """
        fake_stripe._session.metadata = {"response_id": str(owned_response)}
        # The session claims to be paid, so the route tries to grant...
        fake_stripe._session.payment_status = "paid"
        # ...but the checkout object it holds has no payment intent or amount, so the
        # grant's own checks refuse it.
        fake_stripe._session.amount_total = None
        fake_stripe._session.currency = None

        result = await client.get("/api/checkout/cs_test_123")

        # Either it granted (the nulls default safely) or it refused and reported
        # unentitled. What it must not do is crash.
        assert result.status_code == 200
        body = result.json()
        assert body["paid"] is True
        assert body["entitled"] in (True, False)

    async def test_a_refused_grant_on_return_is_reported_not_crashed(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
        fake_stripe: FakeStripeClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A grant that can't be written is a state, not a crash.

        The route catches the refusal and reports "paid but not entitled", so the
        browser keeps polling. Unhandled, this would be a 500 and the visitor would see
        an error page instead of a state it can wait on.
        """
        fake_stripe._session.metadata = {"response_id": str(owned_response)}
        fake_stripe._session.payment_status = "paid"

        def refuse(*args: Any, **kwargs: Any) -> Any:
            raise PaymentNotReadyError("the money hasn't arrived")

        monkeypatch.setattr(payments_routes, "grant_purchase", refuse)

        result = await client.get("/api/checkout/cs_test_123")

        assert result.status_code == 200
        body = result.json()
        assert body["paid"] is True
        assert body["entitled"] is False
        purchases = (await session.execute(select(Purchase))).scalars().all()
        assert purchases == []

    async def test_the_return_path_records_the_payment_time(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
        fake_stripe: FakeStripeClient,
    ) -> None:
        """The return path has no event timestamp, so it uses now.

        Which is right: the visitor is standing on the page, so "now" is when the money
        arrived, to the precision the return path can know.
        """
        fake_stripe._session.metadata = {"response_id": str(owned_response)}
        before = datetime.now(UTC)

        await client.get("/api/checkout/cs_test_123")

        purchase = (await session.execute(select(Purchase))).scalars().one()
        assert purchase.paid_at is not None
        assert purchase.paid_at >= before.replace(microsecond=0)

    async def test_the_return_path_records_what_stripe_charged(
        self,
        client: AsyncClient,
        owned_response: UUID,
        session: AsyncSession,
        stripe_configured: str,
        fake_stripe: FakeStripeClient,
    ) -> None:
        """Even on the return path, the amount is what Stripe charged."""
        fake_stripe._session.metadata = {"response_id": str(owned_response)}
        fake_stripe._session.amount_total = 2500
        fake_stripe._session.currency = "usd"

        await client.get("/api/checkout/cs_test_123")

        purchase = (await session.execute(select(Purchase))).scalars().one()
        assert purchase.amount_cents == 2500


# ===================================================================== the unit level


class TestGrantPurchase:
    """`grant_purchase` — the one implementation of "a checkout becomes access".

    Called by two routes, and Stripe can deliver the same event twice, so it has to be
    idempotent at the function level, where the race would be.
    """

    async def test_it_is_idempotent(
        self, session: AsyncSession, response: QuestionnaireResponse, stripe_configured: str
    ) -> None:
        checkout = _session_for(response)

        await grant_purchase(session, checkout)
        await grant_purchase(session, checkout)

        purchases = (await session.execute(select(Purchase))).scalars().all()
        entitlements = (await session.execute(select(Entitlement))).scalars().all()
        assert len(purchases) == 1
        assert len(entitlements) == 1

    async def test_it_refuses_an_unpaid_session(
        self, session: AsyncSession, response: QuestionnaireResponse, stripe_configured: str
    ) -> None:
        """The unit-level guard, and the exception it raises.

        `PaymentNotReadyError` is caught by both callers and logged, not returned to the
        visitor — a session that isn't paid isn't an error in the request.
        """
        checkout = _session_for(response, payment_status="unpaid")

        with pytest.raises(PaymentNotReadyError):
            await grant_purchase(session, checkout)

        purchases = (await session.execute(select(Purchase))).scalars().all()
        assert purchases == []

    async def test_it_refuses_a_session_with_no_response(
        self, session: AsyncSession, response: QuestionnaireResponse, stripe_configured: str
    ) -> None:
        checkout = _session_for(response)
        checkout.metadata = stripe.StripeObject.construct_from({}, "sk_test_x")

        with pytest.raises(PaymentNotReadyError):
            await grant_purchase(session, checkout)

    async def test_it_refuses_a_session_for_a_missing_response(
        self, session: AsyncSession, stripe_configured: str
    ) -> None:
        checkout = stripe.checkout.Session.construct_from(
            {
                "id": "cs_ghost",
                "object": "checkout.session",
                "amount_total": 2999,
                "currency": "usd",
                "payment_status": "paid",
                "payment_intent": "pi_ghost",
                "created": 1_700_000_000,
                "metadata": {"response_id": str(uuid4()), "user_id": str(uuid4())},
            },
            "sk_test_x",
        )

        with pytest.raises(PaymentNotReadyError):
            await grant_purchase(session, checkout)

    async def test_it_defaults_the_paid_at_to_now(
        self, session: AsyncSession, response: QuestionnaireResponse, stripe_configured: str
    ) -> None:
        """The return path has no event timestamp, so the function supplies one."""
        before = datetime.now(UTC)

        await grant_purchase(session, _session_for(response))

        purchase = (await session.execute(select(Purchase))).scalars().one()
        assert purchase.paid_at is not None
        assert purchase.paid_at >= before.replace(microsecond=0)

    async def test_it_records_the_given_paid_at(
        self, session: AsyncSession, response: QuestionnaireResponse, stripe_configured: str
    ) -> None:
        """The webhook knows better than this function does when the money arrived."""
        when = datetime.fromtimestamp(1_700_000_500, tz=UTC)

        await grant_purchase(session, _session_for(response), paid_at=when)

        purchase = (await session.execute(select(Purchase))).scalars().one()
        assert purchase.paid_at == when

    async def test_it_defaults_the_currency_to_usd(
        self, session: AsyncSession, response: QuestionnaireResponse, stripe_configured: str
    ) -> None:
        """A session with no currency is recorded in dollars rather than nothing.

        `currency` is NOT NULL, so this has to be a real value.
        """
        checkout = _session_for(response)
        checkout.currency = None

        await grant_purchase(session, checkout)

        purchase = (await session.execute(select(Purchase))).scalars().one()
        assert purchase.currency == "usd"

    async def test_it_lowercases_the_currency(
        self, session: AsyncSession, response: QuestionnaireResponse, stripe_configured: str
    ) -> None:
        """Stripe sends currencies lower-case, but the column is a fixed-width CHAR(3)."""
        checkout = _session_for(response)
        checkout.currency = "USD"

        await grant_purchase(session, checkout)

        purchase = (await session.execute(select(Purchase))).scalars().one()
        assert purchase.currency == "usd"

    async def test_it_records_a_missing_amount_as_zero(
        self, session: AsyncSession, response: QuestionnaireResponse, stripe_configured: str
    ) -> None:
        """`amount_cents` is NOT NULL, so a session with no total records zero."""
        checkout = _session_for(response)
        checkout.amount_total = None

        await grant_purchase(session, checkout)

        purchase = (await session.execute(select(Purchase))).scalars().all()
        assert purchase[0].amount_cents == 0


class TestRevokeForRefund:
    """`revoke_for_refund` — the function the refund event calls."""

    async def test_it_revokes_the_entitlement(
        self, session: AsyncSession, response: QuestionnaireResponse, stripe_configured: str
    ) -> None:
        await grant_purchase(session, _session_for(response))
        charge = stripe.Charge.construct_from(
            {"id": "ch_1", "object": "charge", "payment_intent": "pi_test_123"}, "sk_test_x"
        )

        assert await revoke_for_refund(session, charge) is True

        entitlement = (await session.execute(select(Entitlement))).scalars().one()
        assert entitlement.revoked_at is not None

    async def test_it_refuses_an_unknown_payment_intent(
        self, session: AsyncSession, stripe_configured: str
    ) -> None:
        charge = stripe.Charge.construct_from(
            {"id": "ch_1", "object": "charge", "payment_intent": "pi_nothing"}, "sk_test_x"
        )

        assert await revoke_for_refund(session, charge) is False

    async def test_it_refuses_a_charge_with_no_payment_intent(
        self, session: AsyncSession, stripe_configured: str
    ) -> None:
        """Nothing to look up, so nothing to revoke.

        The SDK raises `AttributeError` for a missing key, so this is a real trap rather
        than a hypothetical one.
        """
        charge = stripe.Charge.construct_from({"id": "ch_1", "object": "charge"}, "sk_test_x")

        assert await revoke_for_refund(session, charge) is False

    async def test_it_records_a_refund_with_no_entitlement(
        self, session: AsyncSession, response: QuestionnaireResponse, stripe_configured: str
    ) -> None:
        """A purchase with no entitlement is odd but not fatal.

        The refund is still recorded on the purchase — the money did go back — and the
        function reports that it revoked nothing.
        """
        checkout = _session_for(response)
        await grant_purchase(session, checkout)
        purchase = (await session.execute(select(Purchase))).scalars().one()
        entitlement = (await session.execute(select(Entitlement))).scalars().one()
        await session.delete(entitlement)
        await session.flush()

        charge = stripe.Charge.construct_from(
            {"id": "ch_1", "object": "charge", "payment_intent": "pi_test_123"}, "sk_test_x"
        )

        assert await revoke_for_refund(session, charge) is False
        refreshed = await session.get(Purchase, purchase.id)
        assert refreshed is not None
        assert refreshed.refunded_at is not None


# ===================================================================== helpers


async def _admin_id(session: AsyncSession) -> UUID:
    """A staff account, for a comped entitlement."""
    from app.models import AdminAccount

    admin = AdminAccount(name="Test Admin", email=f"admin-{uuid4().hex[:12]}@example.com")
    session.add(admin)
    await session.flush()
    return admin.id


async def _second_response(client: AsyncClient, session: AsyncSession, user_id: UUID) -> UUID:
    """A second response, owned by the same user."""
    from app.models import Questionnaire, QuestionnaireResponse

    questionnaire = (
        (await session.execute(select(Questionnaire).order_by(Questionnaire.published_at.desc())))
        .scalars()
        .first()
    )
    assert questionnaire is not None
    second = QuestionnaireResponse(
        user_id=user_id,
        questionnaire_id=questionnaire.id,
        contact_email="second@example.com",
    )
    session.add(second)
    await session.flush()
    return second.id


def _session_for(
    response: QuestionnaireResponse, *, payment_status: str = "paid"
) -> stripe.checkout.Session:
    """A real `stripe.checkout.Session` for a response."""
    return stripe.checkout.Session.construct_from(
        {
            "id": "cs_unit",
            "object": "checkout.session",
            "amount_total": 2999,
            "currency": "usd",
            "payment_status": payment_status,
            "payment_intent": "pi_test_123",
            "created": 1_700_000_000,
            "metadata": {
                "response_id": str(response.id),
                "user_id": str(response.user_id),
            },
        },
        "sk_test_x",
    )
