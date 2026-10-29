"""The entitlement gate: has this user paid for this response?

One query, in one place, so no route re-derives what "paid" means. The rule it encodes is
the one from `docs/schema.md`:

```sql
SELECT 1 FROM entitlements
WHERE user_id = :user
  AND (response_id = :response OR response_id IS NULL)
  AND kind IN ('single_report', 'window')
  AND revoked_at IS NULL
  AND now() >= valid_from
  AND now() <  COALESCE(valid_until, 'infinity');
```

Two kinds matter here, and the third deliberately doesn't:

- `single_report` — bought for one response, no expiry. What a per-report purchase issues.
- `window` — bought for a period, covering any response. What a time-window purchase issues.
- `coaching_session` — a placeholder for a future tier. Not access to a report, so it is
  excluded: including it would hand out report access for a booking that hasn't happened.

**`consumed_at` is not part of this.** A `single_report` entitlement is not spent when it's
used — reading a report doesn't consume it, and the bought report stays readable until it is
refunded. `consumed_at` exists for `coaching_session`, where "used" is a real event.

**The kind filter is load-bearing, not decoration.** Without it a `coaching_session` row
would unlock a report, and because `coaching_session` rows are `response_id`-less, it would
unlock *every* report the user has.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Entitlement
from app.models.base import EntitlementKind

# The kinds that grant access to a report. `coaching_session` is a future tier and is not
# one of them.
PAYWALL_KINDS = (EntitlementKind.SINGLE_REPORT, EntitlementKind.WINDOW)


def _active_entitlement_query(user_id: UUID, now: datetime):
    """The gate, as a query. Shared by the two public functions so they can't drift."""
    return select(1).where(
        Entitlement.user_id == user_id,
        Entitlement.kind.in_(PAYWALL_KINDS),
        Entitlement.revoked_at.is_(None),
        Entitlement.valid_from <= now,
        (Entitlement.valid_until.is_(None) | (Entitlement.valid_until > now)),
    )


async def has_active_entitlement(
    session: AsyncSession,
    user_id: UUID,
    response_id: UUID | None = None,
) -> bool:
    """Whether this user has active paid access to this response.

    `response_id` narrows the question: a `single_report` entitlement for *this* response
    counts, and so does a `window` one, which names no response and therefore covers any.

    `response_id=None` asks the narrower question — *window* access only. That is what the
    documented query does when `:response` is bound to NULL: `response_id = NULL` is never
    true, so only the `response_id IS NULL` rows match. Use
    `has_any_active_entitlement` for "any access at all".
    """
    now = datetime.now(UTC)
    query = _active_entitlement_query(user_id, now)
    if response_id is None:
        query = query.where(Entitlement.response_id.is_(None))
    else:
        query = query.where(
            (Entitlement.response_id == response_id) | (Entitlement.response_id.is_(None))
        )

    return await session.scalar(query) is not None


async def has_any_active_entitlement(
    session: AsyncSession,
    user_id: UUID,
) -> bool:
    """Whether this user has any active paid access at all.

    Used where there's no response to narrow by — the questionnaire preview, which asks
    "what may this visitor see?" before one exists.
    """
    now = datetime.now(UTC)
    query = _active_entitlement_query(user_id, now)

    return await session.scalar(query) is not None
