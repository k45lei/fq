"""Tests for the Stripe wiring check.

`app/stripe_check.py` is a diagnostic a person runs when they want to know whether Stripe
is hooked up. It has no bearing on the product, but it does have one on their afternoon: a
half-configured Stripe is a confusing failure, and the whole value of the script is in
telling them *which* piece is missing.

So it's tested like anything else — in particular, that it doesn't lie. A check that says
"ok" when the key is broken is worse than no check at all, because it's trusted.
"""

from typing import Any

import pytest
import stripe
from pytest import MonkeyPatch

from app import stripe_check
from app.stripe_check import Report, check_configuration, check_the_key, check_the_price


class FakeStripeError(stripe.StripeError):
    """A Stripe error with a message, so the checks can report it."""


def test_an_unconfigured_deployment_reports_every_missing_setting(
    monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nothing is set, so all three are named."""
    for name in ("STRIPE_SECRET_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "")
    monkeypatch.setenv("STRIPE_PRICE_ID", "")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "")

    report = Report()
    assert check_configuration(report) is False

    output = capsys.readouterr().out
    assert "STRIPE_SECRET_KEY is not set" in output
    assert "STRIPE_PRICE_ID is not set" in output
    assert "STRIPE_WEBHOOK_SECRET is not set" in output
    assert report.failures == 3


def test_a_fully_configured_deployment_passes(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_something")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_something")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_something")
    stripe_check.get_settings.cache_clear()

    report = Report()
    assert check_configuration(report) is True
    assert report.failures == 0


def test_a_rejected_key_is_reported_as_a_failure(
    monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A key Stripe won't accept must fail the check, not pass it.

    This is the whole point of the script: a typo'd or revoked key looks exactly like a
    working one until you try to charge someone.
    """
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_bogus")
    stripe_check.get_settings.cache_clear()

    class FakeBalance:
        object = "balance"

    class FakeV1:
        class balance:  # noqa: N801 - mirrors the SDK's shape
            @staticmethod
            def retrieve() -> Any:
                raise FakeStripeError("Invalid API Key provided")

    class FakeClient:
        v1 = FakeV1()

    monkeypatch.setattr(stripe_check, "client", lambda: FakeClient())

    report = Report()
    assert check_the_key(report) is False
    assert report.failures == 1
    assert "Stripe rejected the key" in capsys.readouterr().out


def test_an_accepted_key_passes(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_good")
    stripe_check.get_settings.cache_clear()

    class FakeBalance:
        object = "balance"

    class FakeV1:
        class balance:  # noqa: N801 - mirrors the SDK's shape
            @staticmethod
            def retrieve() -> Any:
                return FakeBalance()

    class FakeClient:
        v1 = FakeV1()

    monkeypatch.setattr(stripe_check, "client", lambda: FakeClient())

    report = Report()
    assert check_the_key(report) is True
    assert report.failures == 0


def test_a_price_that_matches_the_display_price_passes(
    monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_good")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_good")
    monkeypatch.setenv("REPORT_PRICE_CENTS", "2999")
    monkeypatch.setenv("REPORT_PRICE_CURRENCY", "usd")
    stripe_check.get_settings.cache_clear()

    class FakePrice:
        unit_amount = 2999
        currency = "usd"

    class FakeV1:
        class prices:  # noqa: N801 - mirrors the SDK's shape
            @staticmethod
            def retrieve(price_id: str) -> Any:
                return FakePrice()

    class FakeClient:
        v1 = FakeV1()

    monkeypatch.setattr(stripe_check, "client", lambda: FakeClient())

    report = Report()
    check_the_price(report)

    assert report.failures == 0
    assert "matches what Stripe will charge" in capsys.readouterr().out


def test_a_price_that_disagrees_with_the_display_price_fails(
    monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The customer would be told one number and charged another.

    This is the check the rollout checklist asks for, done automatically instead of by
    hand.
    """
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_good")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_good")
    monkeypatch.setenv("REPORT_PRICE_CENTS", "1999")
    monkeypatch.setenv("REPORT_PRICE_CURRENCY", "usd")
    stripe_check.get_settings.cache_clear()

    class FakePrice:
        unit_amount = 2999
        currency = "usd"

    class FakeV1:
        class prices:  # noqa: N801 - mirrors the SDK's shape
            @staticmethod
            def retrieve(price_id: str) -> Any:
                return FakePrice()

    class FakeClient:
        v1 = FakeV1()

    monkeypatch.setattr(stripe_check, "client", lambda: FakeClient())

    report = Report()
    check_the_price(report)

    assert report.failures == 1
    output = capsys.readouterr().out
    assert "does NOT match" in output
    assert "1999" in output
    assert "2999" in output
