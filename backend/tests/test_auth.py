"""Tests for Google sign-in.

The OAuth round trip itself is Authlib's and isn't exercised here — mocking Google's token
endpoint would mostly test the mock. What *is* ours, and what these cover, is everything
either side of it: which account a verified identity resolves to, and what a session then
grants.

The linking tests matter most. Accounts usually already exist when someone first signs in,
created by the capture form from an unverified email, and getting that resolution wrong means
either a duplicate account or handing someone else's history over.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import claim_responses, link_google_account
from app.models import User
from app.questionnaires import activate, active_questionnaire_id
from app.responses import start
from app.seed import QuestionnaireSpec, seed_questionnaire
from app.seed_careers import CareerFamiliesSpec, seed_career_families
from tests.factories import create_user, sign_in_directly, start_response
from tests.test_api import valid_answers
from tests.test_ownership import begin, stranger  # noqa: F401 — `stranger` is a fixture
from tests.test_seed import CURRENT_QUESTIONNAIRE, SEEDS_DIR


@pytest.fixture
async def live(session: AsyncSession) -> None:
    await seed_career_families(
        session, CareerFamiliesSpec.from_file(SEEDS_DIR / "career-families.yaml")
    )
    spec = QuestionnaireSpec.from_file(CURRENT_QUESTIONNAIRE)
    questionnaire_id = await seed_questionnaire(session, spec, publish=True)
    await activate(session, questionnaire_id)


# --------------------------------------------------------------------- account linking


async def test_a_new_identity_creates_an_account(session: AsyncSession) -> None:
    user = await link_google_account(
        session, google_sub="sub-1", email="new@example.com", name="New Person"
    )

    assert user.google_sub == "sub-1"
    assert user.email == "new@example.com"


async def test_signing_in_claims_the_account_the_capture_form_made(
    session: AsyncSession,
) -> None:
    """The common case: the row already exists, created from an unverified email."""
    existing = await create_user(session, email="sam@example.com")

    linked = await link_google_account(
        session, google_sub="sub-2", email="sam@example.com", name="Sam From Google"
    )

    assert linked.id == existing.id
    assert linked.google_sub == "sub-2"
    assert await session.scalar(select(func.count()).select_from(User)) == 1


async def test_the_name_already_on_file_is_kept(session: AsyncSession) -> None:
    """What someone typed on the form beats their Google profile name."""
    await create_user(session, email="sam@example.com")

    linked = await link_google_account(
        session, google_sub="sub-3", email="sam@example.com", name="Samantha Q Public"
    )

    assert linked.name == "Test Student"


async def test_matching_is_case_insensitive(session: AsyncSession) -> None:
    existing = await create_user(session, email="Sam@Example.com")

    linked = await link_google_account(
        session, google_sub="sub-4", email="sam@example.com", name=None
    )

    assert linked.id == existing.id


async def test_a_returning_identity_matches_on_sub_not_email(
    session: AsyncSession,
) -> None:
    """Google emails can change; the subject claim can't, so it wins once recorded."""
    await create_user(session, email="old@example.com")
    first = await link_google_account(
        session, google_sub="sub-5", email="old@example.com", name=None
    )

    second = await link_google_account(
        session, google_sub="sub-5", email="changed@example.com", name=None
    )

    assert second.id == first.id
    assert await session.scalar(select(func.count()).select_from(User)) == 1


# --------------------------------------------------------------------- claiming


async def test_signing_in_claims_work_done_under_another_email(
    session: AsyncSession, live: None
) -> None:
    """The case that makes sign-in feel broken otherwise.

    Someone answers the questionnaire as me@school.edu, then signs in with Gmail. Without
    claiming, the report they finished seconds ago is owned by a different row and their
    history is empty.
    """
    anonymous = await create_user(session, email="typed-on-the-form@example.com")
    questionnaire_id = await active_questionnaire_id(session)
    assert questionnaire_id is not None
    response = await start_response(session, questionnaire_id, anonymous)

    signed_in = await link_google_account(
        session, google_sub="sub-claim", email="real@gmail.com", name="Real Person"
    )
    moved = await claim_responses(session, user_id=signed_in.id, response_ids={response.id})

    assert moved == 1
    await session.refresh(response)
    assert response.user_id == signed_in.id


async def test_claiming_leaves_a_signed_in_account_alone(session: AsyncSession, live: None) -> None:
    """A shared browser must not let one person's sign-in take another's work.

    The capability cookie is per browser, so two people on one machine can both hold it. The
    guard is that a response already belonging to a *verified* account is never moved.
    """
    established = await link_google_account(
        session, google_sub="sub-established", email="first@gmail.com", name="First"
    )
    questionnaire_id = await active_questionnaire_id(session)
    assert questionnaire_id is not None
    response = await start_response(session, questionnaire_id, established)

    newcomer = await link_google_account(
        session, google_sub="sub-newcomer", email="second@gmail.com", name="Second"
    )
    moved = await claim_responses(session, user_id=newcomer.id, response_ids={response.id})

    assert moved == 0
    await session.refresh(response)
    assert response.user_id == established.id


async def test_claiming_nothing_is_harmless(session: AsyncSession) -> None:
    user = await link_google_account(
        session, google_sub="sub-empty", email="nobody@gmail.com", name=None
    )

    assert await claim_responses(session, user_id=user.id, response_ids=set()) == 0


async def test_a_signed_in_visitor_keeps_their_account_whatever_they_type(
    session: AsyncSession, live: None
) -> None:
    """The session wins over the submitted email.

    Otherwise someone signed in as one address who types another ends up with the response on
    a different account — missing from their history, and unreachable once the ownership
    cookie is gone. Claiming can't fix it after the fact: that runs at sign-in, and they were
    already signed in.
    """
    account = await link_google_account(
        session, google_sub="sub-typed", email="real@gmail.com", name="Real Person"
    )

    response, _ = await start(
        session,
        name="Someone Else",
        email="a-totally-different@example.com",
        user_id=account.id,
    )

    assert response.user_id == account.id
    # And no stray account was created for the address they typed.
    assert (
        await session.scalar(
            select(User).where(User.email.ilike("a-totally-different@example.com"))
        )
        is None
    )


async def test_an_anonymous_visitor_still_gets_an_account_from_the_form(
    session: AsyncSession, live: None
) -> None:
    response, _ = await start(session, name="Sam", email="sam@example.com")

    owner = await session.get(User, response.user_id)
    assert owner is not None
    assert owner.email == "sam@example.com"


# --------------------------------------------------------------------- contact email


async def test_the_address_asked_for_is_recorded_on_the_response(
    session: AsyncSession, live: None
) -> None:
    response, _ = await start(session, name="Sam", email="sam@school.edu")

    assert response.contact_email == "sam@school.edu"


async def test_a_signed_in_visitor_is_contacted_at_their_account_address(
    session: AsyncSession, live: None
) -> None:
    """They're never shown an email field, so the account's address is what was asked for."""
    account = await link_google_account(
        session, google_sub="sub-contact", email="real@gmail.com", name="Real"
    )

    response, _ = await start(session, name="Real", email="ignored@example.com", user_id=account.id)

    assert response.contact_email == "real@gmail.com"


async def test_the_contact_address_survives_being_claimed(
    session: AsyncSession, live: None
) -> None:
    """The whole point of the column: the account moves, the promise doesn't.

    Someone asks for results at a school address, then signs in with Gmail. The response is
    re-pointed at the Gmail account, but what they asked for is still on record.
    """
    response, _ = await start(session, name="Sam", email="sam@school.edu")

    signed_in = await link_google_account(
        session, google_sub="sub-survives", email="sam@gmail.com", name="Sam"
    )
    moved = await claim_responses(session, user_id=signed_in.id, response_ids={response.id})

    assert moved == 1
    await session.refresh(response)
    assert response.user_id == signed_in.id
    assert response.contact_email == "sam@school.edu"


# --------------------------------------------------------------------- the session


async def test_nobody_is_signed_in_by_default(client: AsyncClient) -> None:
    body = (await client.get("/api/auth/me")).json()

    assert body["signed_in"] is False
    assert body["reports"] == []


async def test_sign_in_is_unavailable_without_credentials(client: AsyncClient) -> None:
    """Google credentials are optional — the rest of the product works without them."""
    response = await client.get("/api/auth/google/login", follow_redirects=False)

    assert response.status_code == 503
    assert "GOOGLE_CLIENT_ID" in response.json()["detail"]


async def test_logging_out_leaves_work_in_progress_alone(client: AsyncClient, live: None) -> None:
    """Signing out ends an identity, not a questionnaire you're part-way through."""
    started: dict[str, Any] = await begin(client)

    assert (await client.post("/api/auth/logout")).status_code == 204

    submitted = await client.post(
        f"/api/responses/{started['response_id']}/sections/interests",
        json={"answers": valid_answers(started["questionnaire"])},
    )
    assert submitted.status_code == 200


# --------------------------------------------------------------------- access via identity


async def test_a_signed_in_user_reaches_their_own_report_without_the_cookie(
    client: AsyncClient,
    stranger: AsyncClient,  # noqa: F811 — imported fixture
    live: None,
    session: AsyncSession,
) -> None:
    """The point of signing in: a second device, with no capability cookie at all."""
    started: dict[str, Any] = await begin(client)
    submitted = await client.post(
        f"/api/responses/{started['response_id']}/sections/interests",
        json={"answers": valid_answers(started["questionnaire"])},
    )
    report_id = submitted.json()["report_id"]

    # Without a session, the second browser is a stranger.
    assert (await stranger.get(f"/api/reports/{report_id}")).status_code == 404

    # Sign that browser in as the account the capture form created.
    owner = await session.scalar(select(User).where(User.email.ilike("sam@example.com")))
    assert owner is not None
    await link_google_account(session, google_sub="sub-6", email=owner.email, name=owner.name)
    sign_in_directly(stranger, owner.id)

    assert (await stranger.get(f"/api/reports/{report_id}")).status_code == 200


async def test_signing_in_does_not_reach_anyone_elses_report(
    client: AsyncClient,
    stranger: AsyncClient,  # noqa: F811 — imported fixture
    live: None,
    session: AsyncSession,
) -> None:
    started: dict[str, Any] = await begin(client)
    submitted = await client.post(
        f"/api/responses/{started['response_id']}/sections/interests",
        json={"answers": valid_answers(started["questionnaire"])},
    )
    report_id = submitted.json()["report_id"]

    someone_else = await create_user(session, email="other@example.com")
    sign_in_directly(stranger, someone_else.id)

    assert (await stranger.get(f"/api/reports/{report_id}")).status_code == 404
