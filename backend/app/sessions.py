"""Per-response ownership, carried in a signed cookie.

**This grants a capability, not an identity.** The cookie says "this browser started these
responses", not "this browser belongs to this user" — and that distinction is load-bearing.

The capture form takes an email and nothing verifies it. `_find_or_create_user` reuses an
existing account when the email matches, so a user-level session would mean that typing a
known address hands you a session as that person, their past reports included. A capability
scoped to the responses you actually started has no such hole: knowing someone's email gets
you nothing, because the cookie is the only thing that grants access.

Account-level access across devices needs the email actually proven. That's Google OAuth, and
it populates `users.google_sub`; until then this is the whole of the access model.
"""

from datetime import timedelta
from uuid import UUID

from fastapi import Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config import get_settings

COOKIE_NAME = "fq_ownership"
COOKIE_MAX_AGE = int(timedelta(days=180).total_seconds())

# Namespaces the signature, so a cookie minted for some other purpose under the same secret
# can never be replayed as an ownership grant.
SALT = "response-ownership.v1"

# A browser only ever needs its recent attempts, and an unbounded list would eventually
# outgrow the 4KB a cookie gets.
MAX_TRACKED = 20


def _serializer() -> URLSafeTimedSerializer:
    secret = get_settings().session_secret
    if secret is None:
        raise RuntimeError(
            "SESSION_SECRET is not set — ownership cookies cannot be signed without it."
        )
    return URLSafeTimedSerializer(secret.get_secret_value(), salt=SALT)


def owned_response_ids(request: Request) -> set[UUID]:
    """The responses this browser started. Empty for anyone without a valid cookie."""
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return set()

    try:
        payload = _serializer().loads(raw, max_age=COOKIE_MAX_AGE)
    except BadSignature:
        # Tampered, or signed with a rotated secret. Treat as no ownership rather than an
        # error: the caller simply gets a 404, which is what a stranger should see anyway.
        return set()

    if not isinstance(payload, list):
        return set()

    owned: set[UUID] = set()
    for item in payload:
        try:
            owned.add(UUID(str(item)))
        except ValueError:
            continue
    return owned


def grant_ownership(request: Request, response: Response, response_id: UUID) -> None:
    """Add a response to this browser's cookie, keeping the most recent `MAX_TRACKED`."""
    existing = [str(item) for item in owned_response_ids(request)]
    updated = [str(response_id), *(item for item in existing if item != str(response_id))]

    settings = get_settings()
    response.set_cookie(
        COOKIE_NAME,
        _serializer().dumps(updated[:MAX_TRACKED]),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        # Lax rather than Strict: the report link is something people follow from an email,
        # and Strict would drop the cookie on that first cross-site navigation.
        samesite="lax",
        secure=settings.environment != "development",
        path="/",
    )
