"""contact email on a response

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-02

Where to send a report is a property of the attempt, not of the account.

The capture form promises "we'll send a copy of your results to that email", and until now the
only record of that address was `users.email` — which moves. Someone who types a school
address and later signs in with Gmail has their response re-pointed at the Gmail account, and
the address they actually asked for is left on an abandoned row. Recording it against the
response keeps the promise intact through any later account juggling.

Backfilled from the owning user, which is where the address came from, then made NOT NULL:
every response was captured with one, so nullable would only invite a missing case that can't
actually occur.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE questionnaire_responses ADD COLUMN contact_email text;

        UPDATE questionnaire_responses AS r
           SET contact_email = u.email
          FROM users AS u
         WHERE u.id = r.user_id;

        ALTER TABLE questionnaire_responses ALTER COLUMN contact_email SET NOT NULL;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE questionnaire_responses DROP COLUMN IF EXISTS contact_email;")
