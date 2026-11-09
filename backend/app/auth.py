"""Google sign-in, and the identity session it establishes.

Two kinds of access exist side by side, and the difference is the whole point:

* **Capability** (`app/sessions.py`) — "this browser started these responses". Issued by the
  capture form, which takes an unverified email, so it can never imply identity.
* **Identity** (here) — "this browser belongs to this user". Only ever issued after Google
  asserts the email, which is what makes account-level access safe.

Signing in needs no linking step. Responses already carry `user_id` from the capture email, so
matching that email to a verified Google account reconnects previous work automatically.

A note on the takeover that isn't one: someone could have typed a stranger's address into the
capture form and created that `users` row. They only ever held a capability for the response
they personally started — never account access. When the real owner signs in, they take the
row over, which is the correct outcome rather than a loss.
"""

from uuid import UUID

from authlib.integrations.starlette_client import OAuth
from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import QuestionnaireResponse, User

GOOGLE_METADATA = "https://accounts.google.com/.well-known/openid-configuration"

SESSION_USER_KEY = "user_id"

_oauth: OAuth | None = None


class GoogleNotConfiguredError(RuntimeError):
    """No client id or secret — sign-in is optional and simply unavailable."""


class UnverifiedEmailError(RuntimeError):
    """Google returned an address it hasn't verified."""


def google() -> OAuth:
    """The configured client, registered on first use.

    Lazily, because the credentials are optional: the rest of the product works without them,
    and registering at import time would make a missing secret a startup failure.
    """
    global _oauth

    settings = get_settings()
    if not settings.google_oauth_configured:
        raise GoogleNotConfiguredError(
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are not set, so sign-in is disabled."
        )

    if _oauth is None:
        registry = OAuth()
        registry.register(
            name="google",
            client_id=settings.google_client_id,
            client_secret=(
                settings.google_client_secret.get_secret_value()
                if settings.google_client_secret
                else None
            ),
            server_metadata_url=GOOGLE_METADATA,
            client_kwargs={"scope": "openid email profile"},
        )
        _oauth = registry

    return _oauth


async def link_google_account(
    session: AsyncSession, *, google_sub: str, email: str, name: str | None
) -> User:
    """Find or create the user behind a verified Google identity.

    Matching is on email rather than `google_sub`, because the account usually already exists:
    it was created by the capture form before anyone signed in. `google_sub` is then recorded
    so later sign-ins match on something that can't change — Google emails can, the subject
    claim can't.
    """
    by_sub = await session.scalar(select(User).where(User.google_sub == google_sub))
    if by_sub is not None:
        return by_sub

    by_email = await session.scalar(select(User).where(User.email.ilike(email)))
    if by_email is not None:
        by_email.google_sub = google_sub
        # Deliberately not overwriting the stored name: what someone typed on the form is
        # more likely what they want to be called than their Google profile name.
        await session.flush()
        return by_email

    user = User(name=name or email.split("@")[0], email=email, google_sub=google_sub)
    session.add(user)
    await session.flush()
    return user


async def claim_responses(session: AsyncSession, *, user_id: UUID, response_ids: set[UUID]) -> int:
    """Attach responses this browser started to the account that just signed in.

    Without this, finishing the questionnaire as `me@school.edu` and then signing in with a
    Gmail address leaves the report orphaned from the account seconds later — technically
    correct, and baffling to the person it happens to.

    The capability is the proof: you hold a cookie saying you started these, and you have now
    proven who you are, so they're yours.

    Only responses whose current owner has never signed in are moved. A shared browser is the
    case that matters — if the row already belongs to a verified account, someone else's
    sign-in must not take it, and the guard makes that impossible rather than unlikely.
    """
    if not response_ids:
        return 0

    result = await session.execute(
        update(QuestionnaireResponse)
        .where(
            QuestionnaireResponse.id.in_(response_ids),
            QuestionnaireResponse.user_id != user_id,
            QuestionnaireResponse.user_id.in_(select(User.id).where(User.google_sub.is_(None))),
        )
        .values(user_id=user_id)
    )
    await session.flush()
    return result.rowcount or 0


def sign_in(request: Request, user: User) -> None:
    request.session[SESSION_USER_KEY] = str(user.id)


def sign_out(request: Request) -> None:
    request.session.pop(SESSION_USER_KEY, None)


def current_user_id(request: Request) -> UUID | None:
    """The signed-in user, or None. Never raises on a malformed session."""
    raw = request.session.get(SESSION_USER_KEY)
    if not raw:
        return None
    try:
        return UUID(str(raw))
    except ValueError:
        return None
