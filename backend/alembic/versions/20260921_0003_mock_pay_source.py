"""allow a mock career_pay source

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-21

Placeholder pay figures for demos need a home that cannot be confused with sourced data.
Recording them as 'bls_oews' would make fabricated numbers indistinguishable from real ones,
which defeats the point of tracking provenance at all — so they get their own source value, and
queries prefer real data wherever both exist.

This is the change `docs/schema.md` had in mind when it argued for text + CHECK over an enum:
a new value is one migration, with none of the ALTER TYPE ... ADD VALUE awkwardness.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE career_pay DROP CONSTRAINT career_pay_source;

        ALTER TABLE career_pay ADD CONSTRAINT career_pay_source
            CHECK (source IN ('bls_oews', 'model_research', 'mock'));
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM career_pay WHERE source = 'mock';

        ALTER TABLE career_pay DROP CONSTRAINT career_pay_source;

        ALTER TABLE career_pay ADD CONSTRAINT career_pay_source
            CHECK (source IN ('bls_oews', 'model_research'));
    """)
