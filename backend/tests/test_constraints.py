"""Tests for the declarative integrity `docs/schema.md` claims.

Each test here corresponds to a claim in the schema doc that something is *unrepresentable*
rather than merely discouraged. They're worth asserting because the mechanisms are subtle —
composite foreign keys and denormalized discriminator columns — and a future simplification
that "tidies up" a redundant UNIQUE constraint would silently remove the protection without
breaking anything else.
"""

from uuid import UUID

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from tests.seed import (
    create_admin,
    create_purchase,
    create_questionnaire,
    create_response,
    create_user,
    question_of_type,
)

FK_VIOLATION = "violates foreign key constraint"


@pytest.fixture
def draft(conn: Connection) -> tuple[UUID, UUID]:
    actor_id = create_admin(conn)
    return create_questionnaire(conn, actor_id), actor_id


# ------------------------------------------------------- an option can only answer its question


def test_an_option_cannot_answer_a_different_question(
    conn: Connection, draft: tuple[UUID, UUID]
) -> None:
    """Hole A in the doc: option_id alone would let a client submit another question's option."""
    questionnaire_id, _ = draft
    user_id = create_user(conn)
    response_id = create_response(conn, user_id, questionnaire_id)

    answered = question_of_type(conn, questionnaire_id, "multi_choice")
    other = question_of_type(conn, questionnaire_id, "single_choice")
    foreign_option = conn.execute(
        text("SELECT id FROM question_options WHERE question_id = :q LIMIT 1"), {"q": other}
    ).scalar_one()

    conn.execute(
        text("""
            INSERT INTO response_answers (response_id, questionnaire_id, question_id)
            VALUES (:r, :q, :question)
        """),
        {"r": response_id, "q": questionnaire_id, "question": answered},
    )

    with pytest.raises(IntegrityError, match=FK_VIOLATION):
        conn.execute(
            text("""
                INSERT INTO response_answer_options (response_id, question_id, option_id)
                VALUES (:r, :question, :option)
            """),
            {"r": response_id, "question": answered, "option": foreign_option},
        )


# ------------------------------------------------- a response can only answer its own version


def test_a_response_cannot_answer_another_questionnaires_question(
    conn: Connection, draft: tuple[UUID, UUID]
) -> None:
    """Hole B in the doc, closed by the denormalized questionnaire_id plus two composite FKs."""
    questionnaire_id, actor_id = draft
    other_questionnaire = create_questionnaire(conn, actor_id, version="v2")
    foreign_question = question_of_type(conn, other_questionnaire, "free_text")

    user_id = create_user(conn)
    response_id = create_response(conn, user_id, questionnaire_id)

    with pytest.raises(IntegrityError, match=FK_VIOLATION):
        conn.execute(
            text("""
                INSERT INTO response_answers (response_id, questionnaire_id, question_id)
                VALUES (:r, :q, :question)
            """),
            {"r": response_id, "q": questionnaire_id, "question": foreign_question},
        )


def test_a_response_cannot_claim_a_questionnaire_it_was_not_started_against(
    conn: Connection, draft: tuple[UUID, UUID]
) -> None:
    questionnaire_id, actor_id = draft
    other_questionnaire = create_questionnaire(conn, actor_id, version="v2")
    foreign_question = question_of_type(conn, other_questionnaire, "free_text")

    user_id = create_user(conn)
    response_id = create_response(conn, user_id, questionnaire_id)

    # Consistent with the question, but not with the response.
    with pytest.raises(IntegrityError, match=FK_VIOLATION):
        conn.execute(
            text("""
                INSERT INTO response_answers (response_id, questionnaire_id, question_id)
                VALUES (:r, :other, :question)
            """),
            {"r": response_id, "other": other_questionnaire, "question": foreign_question},
        )


# --------------------------------------------------- options exist only on choice questions


def test_a_scale_question_cannot_have_options(conn: Connection, draft: tuple[UUID, UUID]) -> None:
    questionnaire_id, _ = draft
    scale_question = question_of_type(conn, questionnaire_id, "scale")

    with pytest.raises(IntegrityError, match="question_options_choice_only"):
        conn.execute(
            text("""
                INSERT INTO question_options (question_id, answer_type, position, label)
                VALUES (:question, 'scale', 1, 'Not allowed')
            """),
            {"question": scale_question},
        )


def test_an_option_cannot_lie_about_its_questions_answer_type(
    conn: Connection, draft: tuple[UUID, UUID]
) -> None:
    """Claiming a choice type to dodge the CHECK runs into the composite FK instead."""
    questionnaire_id, _ = draft
    scale_question = question_of_type(conn, questionnaire_id, "scale")

    with pytest.raises(IntegrityError, match=FK_VIOLATION):
        conn.execute(
            text("""
                INSERT INTO question_options (question_id, answer_type, position, label)
                VALUES (:question, 'multi_choice', 1, 'Still not allowed')
            """),
            {"question": scale_question},
        )


def test_answer_type_cannot_be_changed_while_options_exist(
    conn: Connection, draft: tuple[UUID, UUID]
) -> None:
    """Scale bounds are supplied, so this proves the FK rejects it rather than the CHECK."""
    questionnaire_id, _ = draft
    multi_question = question_of_type(conn, questionnaire_id, "multi_choice")

    with pytest.raises(IntegrityError, match=FK_VIOLATION):
        conn.execute(
            text("""
                UPDATE questions
                   SET answer_type = 'scale', scale_min = 1, scale_max = 5, max_selections = NULL
                 WHERE id = :id
            """),
            {"id": multi_question},
        )


# --------------------------------------------------------------------- question shape checks


def test_a_non_scale_question_cannot_carry_scale_bounds(
    conn: Connection, draft: tuple[UUID, UUID]
) -> None:
    questionnaire_id, actor_id = draft

    with pytest.raises(IntegrityError, match="questions_scale_bounds"):
        conn.execute(
            text("""
                INSERT INTO questions (questionnaire_id, position, category, answer_type,
                                       prompt, scale_min, scale_max, created_by)
                VALUES (:q, 50, 'eq', 'free_text', 'Not a scale', 1, 5, :actor)
            """),
            {"q": questionnaire_id, "actor": actor_id},
        )


def test_only_multi_choice_may_limit_selections(conn: Connection, draft: tuple[UUID, UUID]) -> None:
    questionnaire_id, actor_id = draft

    with pytest.raises(IntegrityError, match="questions_max_selections"):
        conn.execute(
            text("""
                INSERT INTO questions (questionnaire_id, position, category, answer_type,
                                       prompt, max_selections, created_by)
                VALUES (:q, 51, 'interests', 'single_choice', 'Pick one', 3, :actor)
            """),
            {"q": questionnaire_id, "actor": actor_id},
        )


# --------------------------------------------------------------------- entitlements


def test_an_entitlement_needs_exactly_one_origin(
    conn: Connection, draft: tuple[UUID, UUID]
) -> None:
    questionnaire_id, actor_id = draft
    user_id = create_user(conn)
    response_id = create_response(conn, user_id, questionnaire_id)
    purchase_id = create_purchase(conn, user_id)

    # Bought and comped at once is not a thing.
    with pytest.raises(IntegrityError, match="entitlements_origin"):
        conn.execute(
            text("""
                INSERT INTO entitlements (user_id, kind, purchase_id, granted_by, response_id)
                VALUES (:user, 'single_report', :purchase, :actor, :response)
            """),
            {
                "user": user_id,
                "purchase": purchase_id,
                "actor": actor_id,
                "response": response_id,
            },
        )


def test_an_entitlement_from_nowhere_is_rejected(
    conn: Connection, draft: tuple[UUID, UUID]
) -> None:
    questionnaire_id, _ = draft
    user_id = create_user(conn)
    response_id = create_response(conn, user_id, questionnaire_id)

    with pytest.raises(IntegrityError, match="entitlements_origin"):
        conn.execute(
            text("""
                INSERT INTO entitlements (user_id, kind, response_id)
                VALUES (:user, 'single_report', :response)
            """),
            {"user": user_id, "response": response_id},
        )


def test_a_staff_comp_needs_no_purchase(conn: Connection, draft: tuple[UUID, UUID]) -> None:
    """The reason purchase_id is nullable — `manage_purchases` has to be able to comp a report."""
    questionnaire_id, actor_id = draft
    user_id = create_user(conn)
    response_id = create_response(conn, user_id, questionnaire_id)

    entitlement_id = conn.execute(
        text("""
            INSERT INTO entitlements (user_id, kind, granted_by, grant_note, response_id)
            VALUES (:user, 'single_report', :actor, 'Support gesture', :response)
            RETURNING id
        """),
        {"user": user_id, "actor": actor_id, "response": response_id},
    ).scalar_one()

    assert entitlement_id is not None


def test_a_single_report_entitlement_must_name_a_response(conn: Connection) -> None:
    user_id = create_user(conn)
    purchase_id = create_purchase(conn, user_id)

    with pytest.raises(IntegrityError, match="entitlements_shape"):
        conn.execute(
            text("""
                INSERT INTO entitlements (user_id, kind, purchase_id)
                VALUES (:user, 'single_report', :purchase)
            """),
            {"user": user_id, "purchase": purchase_id},
        )


def test_a_window_entitlement_must_expire_and_cover_any_response(conn: Connection) -> None:
    user_id = create_user(conn)
    purchase_id = create_purchase(conn, user_id)

    with pytest.raises(IntegrityError, match="entitlements_shape"):
        conn.execute(
            text("""
                INSERT INTO entitlements (user_id, kind, purchase_id)
                VALUES (:user, 'window', :purchase)
            """),
            {"user": user_id, "purchase": purchase_id},
        )


# --------------------------------------------------------------------- case-insensitive keys


def test_emails_differing_only_in_case_are_the_same_account(conn: Connection) -> None:
    create_user(conn, email="Sam@Example.com")

    with pytest.raises(IntegrityError, match="users_email_lower_idx"):
        create_user(conn, email="sam@example.com")


def test_discount_codes_are_case_insensitive(conn: Connection) -> None:
    conn.execute(text("INSERT INTO discount_codes (code) VALUES ('EarlyBird')"))

    with pytest.raises(IntegrityError, match="discount_codes_code_upper_idx"):
        conn.execute(text("INSERT INTO discount_codes (code) VALUES ('EARLYBIRD')"))
