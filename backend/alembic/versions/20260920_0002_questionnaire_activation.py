"""questionnaire activation

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-20

Which questionnaire is live is *not* a property of a questionnaire. Published rows are
immutable, so a flag on `questionnaires` would be unwritable at exactly the moment you'd want
to set it — and more fundamentally, activation is deployment state with a different lifecycle
than content. Keeping it separate also keeps publishing and activating as distinct decisions,
which is what allows a version to be staged before it goes live.

Append-only: the active questionnaire is the most recent row. Rollback is another row rather
than an edit, and "what was live in March?" is answerable.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        -- Composite FK target. Redundant against the primary key, but it is what lets the
        -- activation row require a *published* questionnaire without a trigger.
        ALTER TABLE questionnaires
            ADD CONSTRAINT questionnaires_id_published_at_key UNIQUE (id, published_at);

        CREATE TABLE questionnaire_activations (
            id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            questionnaire_id           uuid NOT NULL,
            -- Denormalized and NOT NULL, so the foreign key below can only be satisfied by a
            -- questionnaire whose published_at is set. Activating a draft is unrepresentable.
            -- Published rows are immutable, so this copy can never drift.
            questionnaire_published_at timestamptz NOT NULL,

            -- clock_timestamp(), not now(): now() is fixed for the whole transaction, so two
            -- activations in one transaction would tie and "most recent" would be ambiguous.
            activated_at               timestamptz NOT NULL DEFAULT clock_timestamp(),
            activated_by               uuid REFERENCES admin_accounts (id) ON DELETE RESTRICT,

            FOREIGN KEY (questionnaire_id, questionnaire_published_at)
                REFERENCES questionnaires (id, published_at) ON DELETE RESTRICT
        );

        CREATE INDEX questionnaire_activations_recent_idx
            ON questionnaire_activations (activated_at DESC);
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS questionnaire_activations;

        ALTER TABLE questionnaires
            DROP CONSTRAINT IF EXISTS questionnaires_id_published_at_key;
    """)
