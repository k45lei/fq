"""Tests for picking the browser's origin out of ALLOWED_ORIGINS.

The ones that matter are the rejections. `origin_for` reads caller-controlled headers, and the
whole safety argument is that an unlisted value can never come back out of it — if it could,
the OAuth redirect URI built from it would deliver authorization codes to an attacker's host.

`conftest.origins` configures two entries, so selecting the right one is distinguishable from
falling back to the first.
"""

import pytest
from starlette.datastructures import Headers
from starlette.requests import Request

from app.config import get_settings
from app.origins import (
    UnlistedOriginError,
    allowed_origins,
    canonical_origin,
    oauth_redirect_uri,
    origin_for,
)

LOCAL = "http://localhost:5173"
DEMO = "https://demo.crystalball.test"


def make_request(**headers: str) -> Request:
    """A GET to /api/auth/google/login carrying the given headers, and nothing else."""
    raw = Headers(headers).raw
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/auth/google/login",
            "query_string": b"",
            "headers": raw,
            "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 50000),
        }
    )


# ------------------------------------------------------------------ configuration


def test_the_first_entry_is_canonical() -> None:
    assert canonical_origin() == LOCAL
    assert allowed_origins() == [LOCAL, DEMO]


@pytest.mark.parametrize(
    "configured",
    [
        "HTTP://LocalHost:5173",  # case
        "http://localhost:5173/",  # trailing slash
        "  http://localhost:5173  ",  # whitespace
    ],
)
def test_origins_are_normalised(monkeypatch: pytest.MonkeyPatch, configured: str) -> None:
    """Otherwise a stray slash in .env is a setting that silently never matches anything."""
    monkeypatch.setenv("ALLOWED_ORIGINS", configured)
    get_settings.cache_clear()

    assert allowed_origins() == [LOCAL]


@pytest.mark.parametrize(
    "configured",
    [
        "",
        "localhost:5173",  # no scheme
        "http://localhost:5173/app",  # a path, not an origin
    ],
)
def test_nonsense_is_refused_at_startup(monkeypatch: pytest.MonkeyPatch, configured: str) -> None:
    """Loudly, at boot — the alternative is a redirect that fails much later and elsewhere."""
    monkeypatch.setenv("ALLOWED_ORIGINS", configured)
    get_settings.cache_clear()

    with pytest.raises(ValueError):
        get_settings()


# ------------------------------------------------------------------ selection


def test_a_forwarded_host_selects_its_origin() -> None:
    """The deployment-behind-a-proxy case: this is what the teammate's tunnel sends."""
    request = make_request(
        **{"x-forwarded-host": "demo.crystalball.test", "x-forwarded-proto": "https"}
    )
    assert origin_for(request) == DEMO


def test_the_first_hop_wins_in_a_proxy_chain() -> None:
    """Later entries are the proxies themselves; the client-facing host is the first."""
    request = make_request(
        **{
            "x-forwarded-host": "demo.crystalball.test, internal.lb",
            "x-forwarded-proto": "https, http",
        }
    )
    assert origin_for(request) == DEMO


def test_a_referer_selects_its_origin() -> None:
    """A top-level navigation has no Origin header, which is exactly the sign-in link's case."""
    request = make_request(referer=f"{DEMO}/start")
    assert origin_for(request) == DEMO


def test_the_host_header_selects_its_origin() -> None:
    """The dev-proxy case, now that vite.config.ts no longer rewrites Host."""
    request = make_request(host="localhost:5173")
    assert origin_for(request) == LOCAL


def test_a_trailing_slash_in_a_header_still_matches() -> None:
    request = make_request(origin=f"{DEMO}/")
    assert origin_for(request) == DEMO


# ------------------------------------------------------------------ rejection


def test_an_unlisted_forwarded_host_is_ignored() -> None:
    """The open-redirect attempt. A forged header must not become a redirect target."""
    request = make_request(**{"x-forwarded-host": "evil.example.com", "x-forwarded-proto": "https"})
    assert origin_for(request) == canonical_origin()


@pytest.mark.parametrize("header", ["origin", "referer", "host"])
def test_no_header_can_introduce_an_unlisted_origin(header: str) -> None:
    """Every header `_candidates` reads, not just the forwarded one."""
    request = make_request(**{header: "https://evil.example.com"})
    assert origin_for(request) == canonical_origin()


def test_a_listed_origin_wins_over_an_unlisted_one() -> None:
    """A forged X-Forwarded-Host must not shadow a genuine Host that does match."""
    request = make_request(**{"x-forwarded-host": "evil.example.com", "host": "localhost:5173"})
    assert origin_for(request) == LOCAL


def test_a_request_with_no_hints_falls_back() -> None:
    assert origin_for(make_request()) == canonical_origin()


def test_the_callback_lands_back_on_the_origin_it_arrived_on() -> None:
    """The post-sign-in redirect, when the session lost `post_signin_origin`.

    Google redirects to the URI we gave it, so the callback arrives on the origin the browser
    is already using. Preferring the canonical entry instead dropped people on whatever was
    first in ALLOWED_ORIGINS — a different host than their session cookie was set for, so they
    landed looking signed out after signing in successfully.
    """
    request = make_request(
        **{"x-forwarded-host": "demo.crystalball.test", "x-forwarded-proto": "https"}
    )
    assert origin_for(request) == DEMO
    assert origin_for(request) != canonical_origin()


def test_a_scheme_mismatch_does_not_match() -> None:
    """http://demo... is not https://demo... — downgrading the scheme is not a near-miss."""
    request = make_request(**{"x-forwarded-host": "demo.crystalball.test"})
    assert origin_for(request) == canonical_origin()


# ------------------------------------------------------------------ the redirect URI


def test_the_redirect_uri_follows_the_request() -> None:
    """The whole point: a deployment on another domain gets its own callback URI."""
    request = make_request(
        **{"x-forwarded-host": "demo.crystalball.test", "x-forwarded-proto": "https"}
    )
    assert oauth_redirect_uri(request) == f"{DEMO}/api/auth/google/callback"


def test_an_unlisted_origin_refuses_rather_than_falling_back() -> None:
    """The failure that prompted this: falling back guarantees a 500 on the callback.

    The session cookie holding the OAuth state belongs to the host the browser is on, so a
    callback URI naming a different host comes back with no state. Refusing here says so, at
    the point where it can still be fixed.
    """
    request = make_request(**{"x-forwarded-host": "evil.example.com"})

    with pytest.raises(UnlistedOriginError) as raised:
        oauth_redirect_uri(request)

    # The message has to name the host and the list, or it's no better than the 500.
    assert "evil.example.com" in str(raised.value)
    assert "ALLOWED_ORIGINS" in str(raised.value)


def test_a_request_with_no_hints_still_gets_a_redirect_uri() -> None:
    """Nothing to diagnose without a Host header, and no browser sends one that way."""
    assert oauth_redirect_uri(make_request()).startswith(canonical_origin())
