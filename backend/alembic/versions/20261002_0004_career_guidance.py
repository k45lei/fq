"""career guidance

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-02

Per-career next steps, replacing the single trait-derived list the report used to end with.
"Take a design and technology course, build a bike, learn basic statics" is a direction;
"start a portfolio" is a platitude.

Shaped like `career_pay` for the same reasons: careers are model-generated free text, so the
key is a normalized name rather than a foreign key, and `region` is present from day one
because schooling routes are the most region-specific advice here.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE career_guidance (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            normalized_name text NOT NULL,
            region          text NOT NULL,

            -- Each is optional on its own, but a row with none of them is just noise.
            study           text,
            project         text,
            topic           text,

            source          text NOT NULL,
            as_of           date NOT NULL,
            fetched_at      timestamptz NOT NULL DEFAULT now(),

            UNIQUE (normalized_name, region, source),
            CONSTRAINT career_guidance_source
                CHECK (source IN ('authored', 'model_research')),
            CONSTRAINT career_guidance_not_empty CHECK (
                study IS NOT NULL OR project IS NOT NULL OR topic IS NOT NULL
            )
        );

        CREATE INDEX career_guidance_lookup_idx ON career_guidance (normalized_name, region);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS career_guidance;")
