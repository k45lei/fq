"""Google sign-in routes.

Thin on purpose: the OAuth dance is Authlib's, the account logic is `app/auth.py`'s, and what
remains here is translating between HTTP and those two.

The redirect URI and the post-sign-in destination both come from `app/origins.py`, which picks
whichever entry in `ALLOWED_ORIGINS` matches the incoming request. Not `request.url_for`: behind
the dev proxy the browser talks to :5173 while the app sees :8000, so a URI built from the app's
own view of itself is one Google rejects and the browser couldn't reach.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    GoogleNotConfiguredError,
    UnverifiedEmailError,
    claim_responses,
    current_user_id,
    google,
    link_google_account,
    sign_in,
    sign_out,
)
from app.db import get_session
from app.models import QuestionnaireResponse, Report, User
from app.origins import (
    UnlistedOriginError,
    allowed_origins,
    oauth_redirect_uri,
    origin_for,
)
from app.schemas import MeOut, PastReportOut
from app.sessions import owned_response_ids

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

SessionDep = Depends(get_session)


@router.get("/google/login")
async def login(request: Request) -> RedirectResponse:
    try:
        client = google()
        redirect_uri = oauth_redirect_uri(request)
    except (GoogleNotConfiguredError, UnlistedOriginError) as error:
        # Both are configuration, and both are worth saying out loud: this is a browser
        # navigation, so the message is what the person sees, and it names the fix.
        logger.warning("sign-in unavailable: %s", error)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error

    # Stashed in the session so the callback sends the browser back where it started, even
    # though the callback itself arrives from Google and so carries no useful Referer.
    request.session["post_signin_origin"] = origin_for(request)

    return await client.google.authorize_redirect(request, redirect_uri)


@router.get("/google/callback")
async def callback(request: Request, session: AsyncSession = SessionDep) -> RedirectResponse:
    """Complete sign-in and hand the browser back to the app.

    Failures redirect rather than returning JSON — this URL is loaded by the browser as a
    navigation, so a JSON error body would simply be rendered as text at a dead end.
    """
    # Where to send the browser afterwards. The session value was written by `login` and the
    # cookie is signed, but it's re-checked against the list anyway — ALLOWED_ORIGINS may have
    # changed since, and an unlisted origin here would be an open redirect.
    #
    # Falling back to this request's own origin rather than the canonical one: Google redirects
    # to the URI we gave it, which is on the origin the browser is already using, so the
    # request carries the right answer. Preferring the canonical entry instead sent people to
    # whatever happened to be first in ALLOWED_ORIGINS — localhost, for a deployment that
    # listed it first — which is a confusing way to land after signing in successfully, and on
    # a different host than the session cookie was set for, so they'd appear signed out too.
    stashed = request.session.pop("post_signin_origin", None)
    app_origin = stashed if stashed in allowed_origins() else origin_for(request)

    try:
        client = google()
        token = await client.google.authorize_access_token(request)
        claims = token.get("userinfo") or {}

        # Google will return unverified addresses. Trusting one would undo the only thing
        # sign-in is for, since account access is granted on the strength of the email.
        if not claims.get("email_verified"):
            raise UnverifiedEmailError("Google has not verified that email address.")

        user = await link_google_account(
            session,
            google_sub=str(claims["sub"]),
            email=str(claims["email"]),
            name=claims.get("name"),
        )
        # Anything started in this browser follows the person to their account. Otherwise
        # finishing the questionnaire under one email and signing in with another leaves the
        # report orphaned from the account seconds later.
        await claim_responses(session, user_id=user.id, response_ids=owned_response_ids(request))
        await session.commit()
        sign_in(request, user)

    except (GoogleNotConfiguredError, UnverifiedEmailError, KeyError, ValueError) as error:
        # The expected failures: not configured, an address Google won't vouch for, a token
        # response missing a claim.
        logger.warning("sign-in failed: %s: %s", type(error).__name__, error)
        return RedirectResponse(f"{app_origin}/?signin=failed")

    except Exception:
        # Everything else — Authlib's OAuthError and MismatchingStateError, httpx timeouts
        # talking to Google, a database error while linking the account.
        #
        # Broad on purpose. This URL is a browser navigation, so an uncaught exception shows
        # the visitor a 500 page at a dead end, which is the worst available outcome and the
        # one most likely to happen during a demo. `logger.exception` keeps the traceback, so
        # catching it loses nothing except the stack rendered in someone's face.
        logger.exception("sign-in failed unexpectedly")
        return RedirectResponse(f"{app_origin}/?signin=failed")

    return RedirectResponse(app_origin)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request) -> None:
    """Ends the identity session only.

    The capability cookie survives deliberately: it represents work done in this browser, and
    signing out of an account shouldn't lock you out of a questionnaire you're part-way
    through.
    """
    sign_out(request)


@router.get("/me", response_model=MeOut)
async def me(request: Request, session: AsyncSession = SessionDep) -> MeOut:
    """Who's signed in, and what they've completed. Anonymous callers get an empty answer."""
    user_id = current_user_id(request)
    if user_id is None:
        return MeOut(signed_in=False)

    user = await session.get(User, user_id)
    if user is None:
        # The row was deleted under a live session; treat it as signed out rather than 500.
        sign_out(request)
        return MeOut(signed_in=False)

    # Ordered newest first, and the order is *total* — `generated_at` is `now()`, which in
    # Postgres is the transaction's start time, so two reports written in one transaction
    # share a timestamp and "newest" would otherwise be whatever the planner returned first.
    # The tiebreak is the tier: `report_tier` is `('free', 'full')`, so `full` sorts last
    # and therefore first under `DESC`, which is the more complete report. The id is the
    # final tiebreak, so the same account always gets the same order.
    rows = (
        await session.execute(
            select(Report.id, Report.generated_at, Report.tier)
            .join(QuestionnaireResponse, QuestionnaireResponse.id == Report.response_id)
            .where(QuestionnaireResponse.user_id == user_id)
            .order_by(Report.generated_at.desc(), Report.tier.desc(), Report.id.desc())
        )
    ).all()

    return MeOut(
        signed_in=True,
        name=user.name,
        email=user.email,
        reports=[
            PastReportOut(id=row.id, generated_at=row.generated_at, tier=row.tier) for row in rows
        ],
    )
