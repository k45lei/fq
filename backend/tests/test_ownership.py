"""Tests for per-response ownership.

The property these protect: holding a response id is not enough. Before this existed, anyone
who guessed or was shown a UUID could read someone's report and submit answers on their
behalf — and because the capture email is never verified, a user-level session would have
replaced that hole with a worse one.

Every rejection here is a **404**, deliberately. A 403 would confirm the id exists, which is
the one fact an attacker enumerating UUIDs is trying to learn.
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.sessions import COOKIE_NAME
from tests.test_api import valid_answers

__all__ = ["begin", "finish"]


@pytest.fixture
async def stranger() -> AsyncIterator[AsyncClient]:
    """A second browser, against the same app, with its own empty cookie jar.

    Shares the session override installed by the `client` fixture, so it sees the same data —
    it just has no ownership of any of it.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as other:
        yield other


async def begin(client: AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/api/responses", json={"name": "Sam Taylor", "email": "sam@example.com"}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def finish(client: AsyncClient, started: dict[str, Any]) -> str:
    submitted = await client.post(
        f"/api/responses/{started['response_id']}/sections/interests",
        json={"answers": valid_answers(started["questionnaire"])},
    )
    assert submitted.status_code == 200, submitted.text
    return submitted.json()["report_id"]


# --------------------------------------------------------------------- the owner


async def test_starting_a_response_sets_the_cookie(client: AsyncClient, live: None) -> None:
    await begin(client)

    assert COOKIE_NAME in client.cookies


async def test_the_owner_can_submit_and_read(client: AsyncClient, live: None) -> None:
    started = await begin(client)

    report_id = await finish(client, started)
    report = await client.get(f"/api/reports/{report_id}")

    assert report.status_code == 200


async def test_a_second_response_does_not_displace_the_first(
    client: AsyncClient, live: None
) -> None:
    """The cookie tracks several attempts — finishing one must not lock you out of another."""
    first = await begin(client)
    second = await begin(client)

    first_report = await finish(client, first)
    second_report = await finish(client, second)

    assert (await client.get(f"/api/reports/{first_report}")).status_code == 200
    assert (await client.get(f"/api/reports/{second_report}")).status_code == 200


# --------------------------------------------------------------------- everyone else


async def test_a_stranger_cannot_submit_to_someone_elses_response(
    client: AsyncClient, stranger: AsyncClient, live: None
) -> None:
    started = await begin(client)

    response = await stranger.post(
        f"/api/responses/{started['response_id']}/sections/interests",
        json={"answers": valid_answers(started["questionnaire"])},
    )

    assert response.status_code == 404


async def test_a_stranger_cannot_read_someone_elses_report(
    client: AsyncClient, stranger: AsyncClient, live: None
) -> None:
    started = await begin(client)
    report_id = await finish(client, started)

    response = await stranger.get(f"/api/reports/{report_id}")

    assert response.status_code == 404


async def test_an_unowned_report_is_indistinguishable_from_a_missing_one(
    client: AsyncClient, stranger: AsyncClient, live: None
) -> None:
    """Same status and same body, or the difference leaks which ids exist."""
    started = await begin(client)
    report_id = await finish(client, started)

    real_but_not_yours = await stranger.get(f"/api/reports/{report_id}")
    entirely_fictional = await stranger.get("/api/reports/00000000-0000-0000-0000-000000000000")

    assert real_but_not_yours.status_code == entirely_fictional.status_code == 404
    assert real_but_not_yours.json() == entirely_fictional.json()


# --------------------------------------------------------------------- the cookie itself


async def test_a_tampered_cookie_grants_nothing(
    client: AsyncClient, stranger: AsyncClient, live: None
) -> None:
    started = await begin(client)
    report_id = await finish(client, started)

    # The payload is readable — it's signed, not encrypted — so forging one is the obvious
    # attack. The signature is what stops it.
    stranger.cookies.set(COOKIE_NAME, f"{started['response_id']}.forged-signature")
    response = await stranger.get(f"/api/reports/{report_id}")

    assert response.status_code == 404


async def test_no_cookie_at_all_grants_nothing(
    client: AsyncClient, stranger: AsyncClient, live: None
) -> None:
    started = await begin(client)
    report_id = await finish(client, started)

    assert COOKIE_NAME not in stranger.cookies
    assert (await stranger.get(f"/api/reports/{report_id}")).status_code == 404
