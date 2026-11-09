"""FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.db import get_engine, get_session
from app.routes import auth, payments, public


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    # Close pooled connections on shutdown rather than leaving the server to drop them.
    await get_engine().dispose()


app = FastAPI(title="FutureQuest", lifespan=lifespan)

# Carries the OAuth state during sign-in and the user id afterwards. Separate from the
# ownership cookie on purpose: one says who you are, the other says what you started, and
# conflating them is what would make an unverified capture email dangerous.
_settings = get_settings()
app.add_middleware(
    SessionMiddleware,
    secret_key=_settings.session_secret.get_secret_value() if _settings.session_secret else "",
    session_cookie="fq_session",
    https_only=_settings.environment != "development",
    same_site="lax",
)

# The browser app is served from a different origin in development (Vite on :5173), and from
# whatever else a deployment puts in ALLOWED_ORIGINS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(public.router)
app.include_router(auth.router)
app.include_router(payments.router)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@app.get("/health")
async def health(session: SessionDep) -> dict[str, str]:
    """Liveness plus a real database round trip — a reachable app with an unreachable database
    is not healthy, and that distinction is the only reason this endpoint takes a session."""
    await session.execute(text("SELECT 1"))
    return {"status": "ok", "environment": get_settings().environment}
