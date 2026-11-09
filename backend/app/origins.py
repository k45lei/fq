"""Working out which of the configured origins a request is actually using.

Three things have to agree with whatever the browser is looking at: CORS, the OAuth redirect
URI, and where the sign-in callback sends people afterwards. A single configured origin makes
all three wrong the moment the app is served from somewhere else — a colleague's tunnel, a
staging domain — which is what `ALLOWED_ORIGINS` being a list fixes.

**A header never becomes an origin directly; it only selects from the list.** `Host`,
`Origin`, `Referer` and the `X-Forwarded-*` family are all caller-controlled, and an origin
taken straight from one would turn sign-in into an open redirect — the authorization code
would be delivered to whatever host an attacker named. Matching against the configured list
means the worst case is falling back to the first entry.
"""

import logging

from fastapi import Request

from app.config import get_settings

logger = logging.getLogger(__name__)

CALLBACK_PATH = "/api/auth/google/callback"


class UnlistedOriginError(RuntimeError):
    """Sign-in was started from a host that isn't in ALLOWED_ORIGINS.

    Raised rather than falling back, because for OAuth specifically the fallback cannot work:
    the session cookie carrying the OAuth state belongs to the host the browser is actually
    on, so sending Google a callback URI on a *different* host means the state is missing when
    it returns. That surfaces as an opaque 500 on the callback, several steps later and
    nowhere near the cause.
    """


def allowed_origins() -> list[str]:
    return get_settings().allowed_origins


def canonical_origin() -> str:
    """The first configured origin, for when there is no request to learn from.

    Emailing a report happens in a background task long after the browser has gone, so the
    link in it has to be a fixed choice.
    """
    return allowed_origins()[0]


def _candidates(request: Request) -> list[str]:
    """Where the browser might think it is, best evidence first.

    A top-level navigation — which is exactly what following the sign-in link is — carries no
    `Origin` header, so `Referer` does more work here than it usually would.
    """
    headers = request.headers
    found: list[str] = []

    forwarded_host = headers.get("x-forwarded-host")
    if forwarded_host:
        proto = headers.get("x-forwarded-proto", request.url.scheme)
        # Comma-separated means a chain of proxies; the first entry is the client-facing one.
        found.append(f"{proto.split(',')[0].strip()}://{forwarded_host.split(',')[0].strip()}")

    origin = headers.get("origin")
    if origin:
        found.append(origin)

    referer = headers.get("referer")
    if referer:
        parts = referer.split("/")
        if len(parts) >= 3:
            found.append(f"{parts[0]}//{parts[2]}")

    host = headers.get("host")
    if host:
        found.append(f"{request.url.scheme}://{host}")

    return [candidate.strip().rstrip("/").lower() for candidate in found]


def origin_for(request: Request) -> str:
    """The configured origin this request appears to come from, else the canonical one."""
    permitted = allowed_origins()
    candidates = _candidates(request)

    for candidate in candidates:
        if candidate in permitted:
            return candidate

    if candidates:
        # A request that said where it came from, and it wasn't on the list. Warning, not
        # info: this is the deployment-is-misconfigured case, and the way it surfaces
        # otherwise is a 500 on the OAuth callback — the session cookie gets set on the real
        # host while Google sends the browser to the fallback one, so the state doesn't match.
        # Note the scheme: a tunnel terminating TLS needs the https:// form listed.
        logger.warning(
            "request origin %s is not in ALLOWED_ORIGINS (%s); falling back to %s. "
            "Sign-in will not work from that host until it is added here and in the Google "
            "console.",
            candidates[0],
            ", ".join(permitted),
            permitted[0],
        )
    else:
        # Nothing to go on at all — a direct curl, say. Routine.
        logger.debug("request carried no origin hints; using %s", permitted[0])

    return permitted[0]


def oauth_redirect_uri(request: Request) -> str:
    """Where Google should send the browser back.

    Derived from the request, so deploying on a new domain means adding it to
    `ALLOWED_ORIGINS` and to the Google console's authorized redirect URIs — and nothing else.
    Google checks the URI against its own list regardless of what we send, so both lists have
    to carry the entry.

    Raises `UnlistedOriginError` if the request came from a host that isn't configured. See
    the exception for why this one case refuses instead of falling back.
    """
    permitted = allowed_origins()
    candidates = _candidates(request)

    for candidate in candidates:
        if candidate in permitted:
            return f"{candidate}{CALLBACK_PATH}"

    if candidates:
        raise UnlistedOriginError(
            f"Sign-in was started from {candidates[0]}, which is not in ALLOWED_ORIGINS "
            f"({', '.join(permitted)}). Add it there and register "
            f"{candidates[0]}{CALLBACK_PATH} in the Google console, then restart."
        )

    # No hints at all — not a browser, so there's nothing to diagnose. The canonical origin is
    # as good an answer as exists.
    return f"{permitted[0]}{CALLBACK_PATH}"
