"""Tests for the publish-time immutability triggers.

A published questionnaire is frozen in its entirety — the row, its questions, and their options.
A draft is freely editable. These triggers are what make the copy-on-write workflow mandatory
rather than merely documented, so each rejection is worth asserting individually.

Every test that expects a rejection puts the failing statement last: a raised error aborts the
transaction, so nothing can run after it.
"""

from uuid import UUID

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from tests.seed import create_admin, create_questionnaire, publish

PUBLISHED_ROW_REJECTED = "is published and is immutable"
PUBLISHED_CONTENT_REJECTED = "copy it into a new draft version"


def first_question(conn: Connection, questionnaire_id: UUID) -> UUID:
    return conn.execute(
        text("SELECT id FROM questions WHERE questionnaire_id = :q ORDER BY position LIMIT 1"),
        {"q": questionnaire_id},
    ).scalar_one()


def first_option(conn: Connection, questionnaire_id: UUID) -> UUID:
    return conn.execute(
        text("""
            SELECT o.id
              FROM question_options o
              JOIN questions q ON q.id = o.question_id
             WHERE q.questionnaire_id = :q
             ORDER BY q.position, o.position
             LIMIT 1
        """),
        {"q": questionnaire_id},
    ).scalar_one()


@pytest.fixture
def draft(conn: Connection) -> tuple[UUID, UUID]:
    actor_id = create_admin(conn)
    return create_questionnaire(conn, actor_id), actor_id


@pytest.fixture
def published(conn: Connection, draft: tuple[UUID, UUID]) -> tuple[UUID, UUID]:
    questionnaire_id, actor_id = draft
    publish(conn, questionnaire_id, actor_id)
    return questionnaire_id, actor_id


# --------------------------------------------------------------------- the questionnaire row


def test_published_questionnaire_cannot_be_updated(
    conn: Connection, published: tuple[UUID, UUID]
) -> None:
    questionnaire_id, _ = published

    with pytest.raises(DBAPIError, match=PUBLISHED_ROW_REJECTED):
        conn.execute(
            text("UPDATE questionnaires SET nickname = 'renamed' WHERE id = :q"),
            {"q": questionnaire_id},
        )


def test_published_questionnaire_cannot_be_deleted(
    conn: Connection, published: tuple[UUID, UUID]
) -> None:
    questionnaire_id, _ = published

    with pytest.raises(DBAPIError, match=PUBLISHED_ROW_REJECTED):
        conn.execute(text("DELETE FROM questionnaires WHERE id = :q"), {"q": questionnaire_id})


def test_publishing_is_one_way(conn: Connection, published: tuple[UUID, UUID]) -> None:
    """Clearing published_at would reopen editing on everything downstream."""
    questionnaire_id, _ = published

    with pytest.raises(DBAPIError, match=PUBLISHED_ROW_REJECTED):
        conn.execute(
            text("""
                UPDATE questionnaires
                   SET published_at = NULL, published_by = NULL
                 WHERE id = :q
            """),
            {"q": questionnaire_id},
        )


# --------------------------------------------------------------------- its questions


def test_cannot_add_a_question_to_a_published_questionnaire(
    conn: Connection, published: tuple[UUID, UUID]
) -> None:
    questionnaire_id, actor_id = published

    with pytest.raises(DBAPIError, match=PUBLISHED_CONTENT_REJECTED):
        conn.execute(
            text("""
                INSERT INTO questions (questionnaire_id, position, category, answer_type,
                                       prompt, created_by)
                VALUES (:q, 99, 'interests', 'free_text', 'Sneaking this in?', :actor)
            """),
            {"q": questionnaire_id, "actor": actor_id},
        )


def test_cannot_edit_a_question_of_a_published_questionnaire(
    conn: Connection, published: tuple[UUID, UUID]
) -> None:
    questionnaire_id, _ = published
    question_id = first_question(conn, questionnaire_id)

    with pytest.raises(DBAPIError, match=PUBLISHED_CONTENT_REJECTED):
        conn.execute(
            text("UPDATE questions SET prompt = 'reworded' WHERE id = :id"),
            {"id": question_id},
        )


def test_cannot_delete_a_question_of_a_published_questionnaire(
    conn: Connection, published: tuple[UUID, UUID]
) -> None:
    questionnaire_id, _ = published
    question_id = first_question(conn, questionnaire_id)

    with pytest.raises(DBAPIError, match=PUBLISHED_CONTENT_REJECTED):
        conn.execute(text("DELETE FROM questions WHERE id = :id"), {"id": question_id})


# --------------------------------------------------------------------- and their options


def test_cannot_add_an_option_to_a_published_questionnaire(
    conn: Connection, published: tuple[UUID, UUID]
) -> None:
    questionnaire_id, _ = published
    question_id = conn.execute(
        text("""
            SELECT id FROM questions
             WHERE questionnaire_id = :q AND answer_type = 'multi_choice'
             LIMIT 1
        """),
        {"q": questionnaire_id},
    ).scalar_one()

    with pytest.raises(DBAPIError, match=PUBLISHED_CONTENT_REJECTED):
        conn.execute(
            text("""
                INSERT INTO question_options (question_id, answer_type, position, label)
                VALUES (:question, 'multi_choice', 99, 'A late addition')
            """),
            {"question": question_id},
        )


def test_cannot_edit_an_option_of_a_published_questionnaire(
    conn: Connection, published: tuple[UUID, UUID]
) -> None:
    questionnaire_id, _ = published
    option_id = first_option(conn, questionnaire_id)

    with pytest.raises(DBAPIError, match=PUBLISHED_CONTENT_REJECTED):
        conn.execute(
            text("UPDATE question_options SET label = 'relabelled' WHERE id = :id"),
            {"id": option_id},
        )


def test_cannot_delete_an_option_of_a_published_questionnaire(
    conn: Connection, published: tuple[UUID, UUID]
) -> None:
    questionnaire_id, _ = published
    option_id = first_option(conn, questionnaire_id)

    with pytest.raises(DBAPIError, match=PUBLISHED_CONTENT_REJECTED):
        conn.execute(text("DELETE FROM question_options WHERE id = :id"), {"id": option_id})


# --------------------------------------------------------------------- drafts stay editable


def test_a_draft_is_fully_editable(conn: Connection, draft: tuple[UUID, UUID]) -> None:
    """The counterpart that matters: none of the above bites before publication."""
    questionnaire_id, actor_id = draft
    question_id = first_question(conn, questionnaire_id)
    option_id = first_option(conn, questionnaire_id)

    conn.execute(
        text("UPDATE questionnaires SET nickname = 'renamed' WHERE id = :q"),
        {"q": questionnaire_id},
    )
    conn.execute(
        text("UPDATE questions SET prompt = 'reworded' WHERE id = :id"), {"id": question_id}
    )
    conn.execute(
        text("UPDATE question_options SET label = 'relabelled' WHERE id = :id"), {"id": option_id}
    )
    conn.execute(
        text("""
            INSERT INTO questions (questionnaire_id, position, category, answer_type,
                                   prompt, created_by)
            VALUES (:q, 99, 'interests', 'free_text', 'An extra question', :actor)
        """),
        {"q": questionnaire_id, "actor": actor_id},
    )
    conn.execute(text("DELETE FROM question_options WHERE id = :id"), {"id": option_id})

    remaining = conn.execute(
        text("SELECT count(*) FROM questions WHERE questionnaire_id = :q"),
        {"q": questionnaire_id},
    ).scalar_one()
    assert remaining == 5


def test_publishing_a_draft_is_allowed(conn: Connection, draft: tuple[UUID, UUID]) -> None:
    questionnaire_id, actor_id = draft

    publish(conn, questionnaire_id, actor_id)

    published_at = conn.execute(
        text("SELECT published_at FROM questionnaires WHERE id = :q"), {"q": questionnaire_id}
    ).scalar_one()
    assert published_at is not None
