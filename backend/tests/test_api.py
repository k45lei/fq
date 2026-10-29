"""End-to-end tests for the public flow.

These drive the app the way the browser will: start a response, submit the free section, read
the report back. The validation tests matter most — they cover the cardinality rules that the
database deliberately cannot enforce, so this endpoint is the only thing standing between a
malformed submission and a nonsensical report.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AdminAccount, Entitlement
from app.questionnaires import activate, active_questionnaire_id
from app.seed import QuestionnaireSpec, seed_questionnaire
from app.seed_careers import CareerFamiliesSpec, seed_career_families
from tests.factories import create_user, sign_in_directly, start_response
from tests.test_seed import CURRENT_QUESTIONNAIRE, SEEDS_DIR


@pytest.fixture
async def live(session: AsyncSession, admin_account: AdminAccount) -> None:
    """A published, activated questionnaire and the family vocabulary."""
    await seed_career_families(
        session, CareerFamiliesSpec.from_file(SEEDS_DIR / "career-families.yaml")
    )
    spec = QuestionnaireSpec.from_file(CURRENT_QUESTIONNAIRE)
    questionnaire_id = await seed_questionnaire(session, spec, publish=True)
    await activate(session, questionnaire_id, actor_id=admin_account.id)


def valid_answers(questionnaire: dict[str, Any]) -> list[dict[str, Any]]:
    """The simplest submission that satisfies every rule for this section."""
    answers = []
    for question in questionnaire["questions"]:
        answer: dict[str, Any] = {"question_id": question["id"]}
        match question["answer_type"]:
            case "scale":
                answer["numeric"] = question["scale_min"]
            case "free_text":
                answer["text"] = "Something I built."
            case _:
                answer["option_ids"] = [question["options"][0]["id"]]
        answers.append(answer)
    return answers


def first_answer(question: dict[str, Any]) -> dict[str, Any]:
    """One valid answer for a question, of whichever shape it asks for."""
    match question["answer_type"]:
        case "scale":
            return {"numeric": question["scale_min"]}
        case "free_text":
            return {"text": "Something I built."}
        case _:
            return {"option_ids": [question["options"][0]["id"]]}


async def start(client: AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/api/responses",
        json={"name": "Sam Taylor", "email": "sam@example.com", "school": "Hill Academy"},
    )
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------- serving


async def test_the_questionnaire_is_unavailable_until_something_is_activated(
    client: AsyncClient,
) -> None:
    response = await client.get("/api/questionnaire")

    assert response.status_code == 503


async def test_only_the_free_section_is_served(client: AsyncClient, live: None) -> None:
    response = await client.get("/api/questionnaire")

    assert response.status_code == 200
    body = response.json()
    assert body["sections"] == ["interests"]
    assert {question["category"] for question in body["questions"]} == {"interests"}


async def test_scoring_internals_are_never_sent_to_the_browser(
    client: AsyncClient, live: None
) -> None:
    """Exposing trait or score_value would let anyone work out how to get a given result."""
    body = (await client.get("/api/questionnaire")).json()

    serialised = str(body)
    assert "trait" not in serialised
    assert "score_value" not in serialised
    assert "reverse_scored" not in serialised


async def test_questions_come_back_in_presentation_order(client: AsyncClient, live: None) -> None:
    body = (await client.get("/api/questionnaire")).json()

    positions = [question["position"] for question in body["questions"]]
    assert positions == sorted(positions)


# --------------------------------------------------------------------- starting


async def test_starting_returns_a_response_and_the_questions(
    client: AsyncClient, live: None
) -> None:
    body = await start(client)

    assert body["response_id"]
    assert body["questionnaire"]["questions"]


async def test_a_malformed_email_is_rejected(client: AsyncClient, live: None) -> None:
    response = await client.post("/api/responses", json={"name": "Sam", "email": "not-an-email"})

    assert response.status_code == 422


async def test_starting_twice_with_one_email_reuses_the_account(
    client: AsyncClient, live: None
) -> None:
    """Emails are uniquely indexed, so a returning visitor must not collide."""
    first = await start(client)
    second = await start(client)

    assert first["response_id"] != second["response_id"]


# --------------------------------------------------------------------- submitting


async def test_submitting_the_free_section_produces_a_report(
    client: AsyncClient, live: None
) -> None:
    started = await start(client)

    response = await client.post(
        f"/api/responses/{started['response_id']}/sections/interests",
        json={"answers": valid_answers(started["questionnaire"])},
    )

    assert response.status_code == 200, response.text
    assert response.json()["report_id"]


async def test_every_question_must_be_answered(client: AsyncClient, live: None) -> None:
    started = await start(client)
    answers = valid_answers(started["questionnaire"])[:-1]

    response = await client.post(
        f"/api/responses/{started['response_id']}/sections/interests", json={"answers": answers}
    )

    assert response.status_code == 422
    assert "not answered" in response.json()["detail"]


async def test_a_scale_answer_outside_its_bounds_is_rejected(
    client: AsyncClient, live: None
) -> None:
    started = await start(client)
    answers = valid_answers(started["questionnaire"])
    scale = next(q for q in started["questionnaire"]["questions"] if q["answer_type"] == "scale")
    for answer in answers:
        if answer["question_id"] == scale["id"]:
            answer["numeric"] = scale["scale_max"] + 1

    response = await client.post(
        f"/api/responses/{started['response_id']}/sections/interests", json={"answers": answers}
    )

    assert response.status_code == 422
    assert "takes" in response.json()["detail"]


async def test_exceeding_max_selections_is_rejected(client: AsyncClient, live: None) -> None:
    started = await start(client)
    answers = valid_answers(started["questionnaire"])
    multi = next(
        q
        for q in started["questionnaire"]["questions"]
        if q["answer_type"] == "multi_choice" and q["max_selections"]
    )
    for answer in answers:
        if answer["question_id"] == multi["id"]:
            answer["option_ids"] = [o["id"] for o in multi["options"]]

    response = await client.post(
        f"/api/responses/{started['response_id']}/sections/interests", json={"answers": answers}
    )

    assert response.status_code == 422
    assert "at most" in response.json()["detail"]


async def test_a_single_choice_question_takes_exactly_one_option(
    client: AsyncClient, live: None
) -> None:
    started = await start(client)
    answers = valid_answers(started["questionnaire"])
    single = next(
        q for q in started["questionnaire"]["questions"] if q["answer_type"] == "single_choice"
    )
    for answer in answers:
        if answer["question_id"] == single["id"]:
            answer["option_ids"] = [o["id"] for o in single["options"]]

    response = await client.post(
        f"/api/responses/{started['response_id']}/sections/interests", json={"answers": answers}
    )

    assert response.status_code == 422
    assert "exactly one" in response.json()["detail"]


async def test_an_option_from_another_question_is_rejected(client: AsyncClient, live: None) -> None:
    started = await start(client)
    questions = started["questionnaire"]["questions"]
    answers = valid_answers(started["questionnaire"])

    single = next(q for q in questions if q["answer_type"] == "single_choice")
    multi = next(q for q in questions if q["answer_type"] == "multi_choice")
    for answer in answers:
        if answer["question_id"] == single["id"]:
            answer["option_ids"] = [multi["options"][0]["id"]]

    response = await client.post(
        f"/api/responses/{started['response_id']}/sections/interests", json={"answers": answers}
    )

    assert response.status_code == 422
    assert "unknown option" in response.json()["detail"]


async def test_a_section_cannot_be_submitted_twice(client: AsyncClient, live: None) -> None:
    started = await start(client)
    payload = {"answers": valid_answers(started["questionnaire"])}
    await client.post(f"/api/responses/{started['response_id']}/sections/interests", json=payload)

    response = await client.post(
        f"/api/responses/{started['response_id']}/sections/interests", json=payload
    )

    assert response.status_code == 422
    assert "already been submitted" in response.json()["detail"]


async def test_submitting_to_an_unknown_response_is_a_404(client: AsyncClient, live: None) -> None:
    response = await client.post(
        "/api/responses/00000000-0000-0000-0000-000000000000/sections/interests",
        json={"answers": [{"question_id": "00000000-0000-0000-0000-000000000000"}]},
    )

    assert response.status_code == 404


# --------------------------------------------------------------------- reading the report


async def test_the_report_reads_back_with_every_section(client: AsyncClient, live: None) -> None:
    started = await start(client)
    submitted = await client.post(
        f"/api/responses/{started['response_id']}/sections/interests",
        json={"answers": valid_answers(started["questionnaire"])},
    )
    report_id = submitted.json()["report_id"]

    response = await client.get(f"/api/reports/{report_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["tier"] == "free"
    assert set(body["payload"]) == {
        "holland_code",
        "traits",
        "strengths",
        "families",
        "careers",
        "challenges",
    }


async def test_an_unknown_report_is_a_404(client: AsyncClient, live: None) -> None:
    response = await client.get("/api/reports/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404


# --------------------------------------------------------------------- entitlement gate tests
# These tests verify that the entitlement gate works correctly.


async def test_a_paid_section_is_refused_by_default(client: AsyncClient, live: None) -> None:
    started = await start(client)

    response = await client.post(
        f"/api/responses/{started['response_id']}/sections/strengths",
        json={"answers": [{"question_id": "00000000-0000-0000-0000-000000000000"}]},
    )

    assert response.status_code == 403
    assert "free report" in response.json()["detail"]


async def test_paid_sections_are_not_served_by_default(client: AsyncClient, live: None) -> None:
    body = (await client.get("/api/questionnaire")).json()

    assert {question["category"] for question in body["questions"]} == {"interests"}


async def test_paid_sections_are_served_with_entitlement(
    client: AsyncClient, live: None, session: AsyncSession, admin_account: AdminAccount
) -> None:
    """An entitlement grants access to all sections.

    The identity has to be real: the gate looks the entitlement up by *user*, so a test that
    faked the session cookie with a bare UUID would look entitled to nobody and pass for the
    wrong reason.
    """
    owner = await create_user(session, email="sam@example.com")
    sign_in_directly(client, owner.id)

    started = await start(client)
    session.add(
        Entitlement(
            user_id=owner.id,
            response_id=started["response_id"],
            kind="single_report",
            valid_until=datetime.now(UTC) + timedelta(days=30),
            granted_by=admin_account.id,
        )
    )
    await session.flush()

    # The response id is passed, because the gate looks the entitlement up by user *and*
    # response: a `single_report` names one response, so a preview with no response to ask
    # about can't widen. The response is the thing that was bought.
    body = (await client.get(f"/api/questionnaire?response_id={started['response_id']}")).json()

    assert body["sections"] == ["interests", "strengths", "eq"]
    assert {question["category"] for question in body["questions"]} == {
        "interests",
        "strengths",
        "eq",
    }


async def test_paid_sections_are_submitted_with_entitlement(
    client: AsyncClient, live: None, session: AsyncSession, admin_account: AdminAccount
) -> None:
    """Each section is submitted on its own, and each produces a report."""
    owner = await create_user(session, email="sam@example.com")
    sign_in_directly(client, owner.id)

    started = await start(client)
    session.add(
        Entitlement(
            user_id=owner.id,
            response_id=started["response_id"],
            kind="single_report",
            valid_until=datetime.now(UTC) + timedelta(days=30),
            granted_by=admin_account.id,
        )
    )
    await session.flush()

    # The sections a visitor may answer are decided when the response is created, so the
    # questionnaire is re-read once the entitlement exists. The submit route re-checks the
    # gate per section, which is what this is really about.
    body = (await client.get(f"/api/questionnaire?response_id={started['response_id']}")).json()
    questions = body["questions"]

    report_ids = []
    for section in body["sections"]:
        in_section = [q for q in questions if q["category"] == section]
        response = await client.post(
            f"/api/responses/{started['response_id']}/sections/{section}",
            json={"answers": valid_answers({"questions": in_section})},
        )
        assert response.status_code == 200, response.text
        report_ids.append(response.json()["report_id"])

    assert len(set(report_ids)) == 3

    # The last one was built from every answer, which is why it's the one shown.
    final = await client.get(f"/api/reports/{report_ids[-1]}")
    assert final.status_code == 200
    assert final.json()["payload"]["holland_code"]


async def test_an_entitlement_for_another_response_grants_nothing(
    client: AsyncClient, live: None, session: AsyncSession, admin_account: AdminAccount
) -> None:
    """The gate is scoped to a response, not to the account.

    Someone who paid for one assessment has paid for *that* assessment. Without this, one
    purchase would unlock every attempt the account has ever made — and the account is
    exactly the thing an entitlement is looked up by, so the scoping has to be enforced here.
    """
    owner = await create_user(session, email="sam@example.com")
    sign_in_directly(client, owner.id)

    started = await start(client)
    questionnaire_id = await active_questionnaire_id(session)
    assert questionnaire_id is not None
    elsewhere = await start_response(session, questionnaire_id, owner)

    # An entitlement against a different response, on the same account.
    session.add(
        Entitlement(
            user_id=owner.id,
            response_id=elsewhere.id,
            kind="single_report",
            valid_until=datetime.now(UTC) + timedelta(days=30),
            granted_by=admin_account.id,
        )
    )
    await session.flush()

    refused = await client.post(
        f"/api/responses/{started['response_id']}/sections/strengths",
        json={"answers": [{"question_id": "00000000-0000-0000-0000-000000000000"}]},
    )
    assert refused.status_code == 403


async def test_answers_for_another_section_are_still_refused(
    client: AsyncClient, live: None, session: AsyncSession, admin_account: AdminAccount
) -> None:
    """Unlocking the sections must not blur the boundary between them.

    The rule that survives the paywall is per-question cardinality: a `multi_choice` question
    takes at most `max_selections` options, and the server checks it. The database can't —
    the options live in a join table — so this endpoint is the only thing standing between a
    malformed submission and a nonsensical report.
    """
    owner = await create_user(session, email="sam@example.com")
    sign_in_directly(client, owner.id)

    started = await start(client)
    session.add(
        Entitlement(
            user_id=owner.id,
            response_id=started["response_id"],
            kind="single_report",
            valid_until=datetime.now(UTC) + timedelta(days=30),
            granted_by=admin_account.id,
        )
    )
    await session.flush()

    body = (await client.get("/api/questionnaire")).json()
    interests = [q for q in body["questions"] if q["category"] == "interests"]
    multi = next(
        (
            q
            for q in interests
            if q["answer_type"] == "multi_choice"
            and q["max_selections"] is not None
            and len(q["options"]) > q["max_selections"]
        ),
        None,
    )
    if multi is None:
        pytest.skip("no multi-choice question with a selection limit in the interests section")

    too_many = [
        {
            "question_id": multi["id"],
            "option_ids": [
                option["id"] for option in multi["options"][: multi["max_selections"] + 1]
            ],
        }
    ]
    everything_else = [
        {"question_id": q["id"], **first_answer(q)} for q in interests if q["id"] != multi["id"]
    ]

    response = await client.post(
        f"/api/responses/{started['response_id']}/sections/interests",
        json={"answers": [*everything_else, *too_many]},
    )

    assert response.status_code == 422
    assert "at most" in response.json()["detail"]
