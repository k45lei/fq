"""Detects models drifting away from the migrated schema.

Nothing else would catch this. `target_metadata` is `None` in `alembic/env.py` and autogenerate
is never used, so the models are never compared against the database in normal operation — a
column added in a migration but not in a model (or vice versa) would simply be wrong, quietly,
until something failed at runtime.

What is compared: tables and columns existing on both sides, and nullability. What is not:
column types, indexes, unique constraints, CHECK constraints and triggers. Types and indexes
round-trip through reflection unreliably enough to produce false failures — particularly enum
arrays and functional indexes — and CHECKs and triggers are invisible to Alembic entirely. The
migration remains the source of truth for all of those; see `app/models/base.py`.
"""

from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine

from app.models import Base

# Reliable to compare, and each one is real drift rather than a reflection artefact.
MEANINGFUL_DIFFS = {
    "add_table",
    "remove_table",
    "add_column",
    "remove_column",
    "modify_nullable",
}

# Alembic's own bookkeeping table is in the database but deliberately not in the models.
IGNORED_TABLES = {"alembic_version"}


def _flatten(diffs: list[Any]) -> list[tuple]:
    """compare_metadata groups some diffs into sub-lists; flatten to one level."""
    flat: list[tuple] = []
    for diff in diffs:
        if isinstance(diff, list):
            flat.extend(diff)
        else:
            flat.append(diff)
    return flat


def _table_name(diff: tuple) -> str:
    if diff[0] in ("add_table", "remove_table"):
        return str(diff[1].name)
    # (kind, schema, table_name, ...)
    return str(diff[2])


def _describe(diff: tuple) -> str:
    kind = diff[0]
    if kind in ("add_table", "remove_table"):
        return f"{kind}: {diff[1].name}"
    if kind in ("add_column", "remove_column"):
        return f"{kind}: {diff[2]}.{diff[3].name}"
    return f"{kind}: {diff[2]}.{diff[3]}"


def test_models_match_the_migrated_schema(engine: Engine) -> None:
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        diffs = _flatten(compare_metadata(context, Base.metadata))

    drift = [
        diff
        for diff in diffs
        if diff[0] in MEANINGFUL_DIFFS and _table_name(diff) not in IGNORED_TABLES
    ]

    assert not drift, (
        "models and migration disagree:\n  "
        + "\n  ".join(_describe(diff) for diff in drift)
        + "\n\n'add_*' means the models declare something the database lacks; "
        "'remove_*' means the database has something the models don't."
    )


def test_every_model_is_registered(engine: Engine) -> None:
    """Guards the drift test itself: an unimported model registers nothing and is never checked."""
    expected = {
        "admin_accounts",
        "career_families",
        "career_guidance",
        "career_pay",
        "discount_codes",
        "entitlements",
        "purchases",
        "question_options",
        "questionnaire_activations",
        "questionnaire_responses",
        "questionnaires",
        "questions",
        "report_career_families",
        "reports",
        "response_answer_options",
        "response_answers",
        "users",
    }

    assert set(Base.metadata.tables) == expected
