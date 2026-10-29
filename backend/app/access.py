"""Who may see a response.

One function, used by every route that touches someone's data, so the rule lives in one place
rather than being re-derived at each call site.

Two ways to qualify, and they're genuinely different claims:

* you hold the **capability** — this browser started the response;
* you are the **user** it belongs to, proven by signing in.

Either is sufficient. The first covers the anonymous flow, the second covers coming back on
another device. Neither can be obtained by guessing an id.
"""

from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user_id
from app.models import QuestionnaireResponse
from app.sessions import owned_response_ids


async def may_access_response(db: AsyncSession, request: Request, response_id: UUID) -> bool:
    if response_id in owned_response_ids(request):
        return True

    user_id = current_user_id(request)
    if user_id is None:
        return False

    owner = await db.scalar(
        select(QuestionnaireResponse.user_id).where(QuestionnaireResponse.id == response_id)
    )
    return owner is not None and owner == user_id
