"""Tests for `fork_questionnaire()`.

The structural-equality test below is the one that matters. `fork_questionnaire()` enumerates
its columns in two `INSERT ... SELECT` statements, so adding a column to `questions` or
`question_options` without adding it there is a silent data-loss bug: the copy comes out
structurally complete and quietly wrong, with the missing column falling back to its default.

The column list is read from `information_schema` rather than hardcoded here — a hardcoded list
in the test would rot at exactly the same rate as the one in the function, and catch nothing.
"""

from uuid import UUID

import pytest
from sqlalchemy import Connection, Row, text
from sqlalchemy.exc import DBAPIError

from tests.seed import create_admin, create_questionnaire, publish

# Expected to differ between an original and its copy.
QUESTION_EXCLUSIONS = {
    "id",
    "questionnaire_id",
    "created_at",
    "created_by",
    "updated_at",
    "updated_by",
}
OPTION_EXCLUSIONS = {"id", "question_id"}


def comparable_columns(conn: Connection, table: str, exclusions: set[str]) -> list[str]:
    columns = (
        conn.execute(
            text("""
                SELECT column_name
                  FROM information_schema.columns
                 WHERE table_schema = 'public' AND table_name = :table
                 ORDER BY ordinal_position
            """),
            {"table": table},
        )
        .scalars()
        .all()
    )

    # If a column is renamed, the exclusion silently stops applying and the test starts
    # comparing something volatile. Fail loudly instead.
    missing = exclusions - set(columns)
    assert not missing, f"{table} has no column(s) {sorted(missing)} — exclusion list is stale"

    return [c for c in columns if c not in exclusions]


def questions_snapshot(conn: Connection, questionnaire_id: UUID, columns: list[str]) -> list[Row]:
    selected = ", ".join(f"q.{c}" for c in columns)
    return list(
        conn.execute(
            text(f"""
                SELECT {selected}
                  FROM questions q
                 WHERE q.questionnaire_id = :q
                 ORDER BY q.position
            """),
            {"q": questionnaire_id},
        ).fetchall()
    )


def options_snapshot(conn: Connection, questionnaire_id: UUID, columns: list[str]) -> list[Row]:
    selected = ", ".join(f"o.{c}" for c in columns)
    return list(
        conn.execute(
            text(f"""
                SELECT {selected}
                  FROM question_options o
                  JOIN questions q ON q.id = o.question_id
                 WHERE q.questionnaire_id = :q
                 ORDER BY q.position, o.position
            """),
            {"q": questionnaire_id},
        ).fetchall()
    )


def fork(conn: Connection, source_id: UUID, actor_id: UUID, version: str = "v2") -> UUID:
    return conn.execute(
        text("SELECT fork_questionnaire(:source, :version, :actor)"),
        {"source": source_id, "version": version, "actor": actor_id},
    ).scalar_one()


@pytest.fixture
def published_questionnaire(conn: Connection) -> tuple[UUID, UUID]:
    actor_id = create_admin(conn)
    questionnaire_id = create_questionnaire(conn, actor_id)
    publish(conn, questionnaire_id, actor_id)
    return questionnaire_id, actor_id


def test_fork_copies_every_question_column(
    conn: Connection, published_questionnaire: tuple[UUID, UUID]
) -> None:
    source_id, actor_id = published_questionnaire
    columns = comparable_columns(conn, "questions", QUESTION_EXCLUSIONS)

    draft_id = fork(conn, source_id, actor_id)

    assert questions_snapshot(conn, draft_id, columns) == questions_snapshot(
        conn, source_id, columns
    )


def test_fork_copies_every_option_column(
    conn: Connection, published_questionnaire: tuple[UUID, UUID]
) -> None:
    source_id, actor_id = published_questionnaire
    columns = comparable_columns(conn, "question_options", OPTION_EXCLUSIONS)

    draft_id = fork(conn, source_id, actor_id)

    assert options_snapshot(conn, draft_id, columns) == options_snapshot(conn, source_id, columns)


def test_fork_gives_every_copied_row_a_new_identity(
    conn: Connection, published_questionnaire: tuple[UUID, UUID]
) -> None:
    source_id, actor_id = published_questionnaire

    draft_id = fork(conn, source_id, actor_id)

    assert draft_id != source_id

    overlapping = conn.execute(
        text("""
            SELECT count(*)
              FROM questions a
              JOIN questions b ON a.id = b.id
             WHERE a.questionnaire_id = :source AND b.questionnaire_id = :draft
        """),
        {"source": source_id, "draft": draft_id},
    ).scalar_one()
    assert overlapping == 0


def test_fork_options_attach_to_the_copied_questions(
    conn: Connection, published_questionnaire: tuple[UUID, UUID]
) -> None:
    """The option copy maps old question to new through `position`. Verify it didn't cross wires."""
    source_id, actor_id = published_questionnaire

    draft_id = fork(conn, source_id, actor_id)

    mismatched = conn.execute(
        text("""
            SELECT count(*)
              FROM question_options o
              JOIN questions q ON q.id = o.question_id
             WHERE q.questionnaire_id = :draft
               AND (q.answer_type, q.position) NOT IN (
                   SELECT answer_type, position FROM questions WHERE questionnaire_id = :source
               )
        """),
        {"draft": draft_id, "source": source_id},
    ).scalar_one()
    assert mismatched == 0


def test_fork_produces_an_unpublished_draft_with_lineage(
    conn: Connection, published_questionnaire: tuple[UUID, UUID]
) -> None:
    source_id, actor_id = published_questionnaire

    draft_id = fork(conn, source_id, actor_id, version="v2")

    row = conn.execute(
        text("""
            SELECT nickname, version, derived_from_id, published_at, published_by, created_by
              FROM questionnaires WHERE id = :id
        """),
        {"id": draft_id},
    ).one()

    assert row.nickname == "test-instrument"
    assert row.version == "v2"
    assert row.derived_from_id == source_id
    assert row.published_at is None
    assert row.published_by is None
    assert row.created_by == actor_id


def test_fork_of_a_missing_questionnaire_raises(conn: Connection) -> None:
    actor_id = create_admin(conn)

    with pytest.raises(DBAPIError, match="not found"):
        conn.execute(
            text("SELECT fork_questionnaire(gen_random_uuid(), 'v2', :actor)"),
            {"actor": actor_id},
        )
