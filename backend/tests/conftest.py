"""Test fixtures.

Tests run against a real Postgres — never SQLite. Most of this schema's correctness lives in
enums, arrays, CHECK constraints, plpgsql triggers and deferrable unique constraints, none of
which SQLite has, so a test that avoided Postgres would exercise almost nothing.

The suite uses its own database (`<dev database>_test`), dropped and re-migrated once per
session. Each test runs inside a transaction that is rolled back afterwards, so tests don't see
each other's rows.

Migrations run under the sync psycopg driver, as they do from the CLI — see `alembic/env.py`.
"""

import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

# Settings now require a session secret, since ownership cookies are signed with it. Set one
# before anything imports app.config, so the suite never depends on a developer's .env.
os.environ.setdefault("SESSION_SECRET", "test-secret-not-used-outside-the-suite")

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app import auth
from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models import AdminAccount, Questionnaire, QuestionnaireResponse, User
from app.questionnaires import activate
from app.seed import QuestionnaireSpec, seed_questionnaire
from app.seed_careers import CareerFamiliesSpec, seed_career_families
from tests.test_seed import CURRENT_QUESTIONNAIRE, SEEDS_DIR

__all__ = ["get_settings"]

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _test_database_url() -> URL:
    """The dev database URL, with the sync driver and a `_test` suffix on the database name."""
    url = make_url(get_settings().database_url.get_secret_value()).set(
        drivername="postgresql+psycopg"
    )
    return url.set(database=f"{url.database}_test")


def _render(url: URL) -> str:
    return url.render_as_string(hide_password=False)


@pytest.fixture(autouse=True)
def locked(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The paywall is on, whatever the developer's .env says.

    Global rather than per-module: the demo bypass that used to live here is gone, and the
    gate is now unconditional — there is no setting that can turn it off. The fixture stays
    because it is the natural place to force the settings the gate depends on, and because
    clearing the settings cache around every test is what keeps a developer's `.env` from
    leaking into a test.
    """
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def origins(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A known origin list, whatever the developer's .env says.

    Same reasoning as `locked`. A developer running this behind a tunnel would otherwise have
    report links and redirects pointing at their domain, and anything asserting on either
    would pass only for them.

    Two entries, because one would make the matching tests pass by accident — with a single
    origin, falling back to the canonical one is indistinguishable from selecting correctly.
    """
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:5173,https://demo.crystalball.test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def google_unconfigured(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No Google credentials, whatever the developer's .env says.

    Same reasoning as `locked`. Nothing here exercises the real OAuth round trip, so the only
    thing live credentials could do is make behaviour differ between a machine that has them
    and one that doesn't — which is how a test ends up passing in CI and failing locally.

    `_oauth` is reset too: it caches the registered client, and a registration leaking between
    tests would survive the credentials being cleared.
    """
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "")
    monkeypatch.setattr(auth, "_oauth", None, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    target = _test_database_url()

    # Belt and braces: this fixture drops a database, so refuse to run against anything that
    # isn't obviously the test one. A misconfigured DATABASE_URL should fail, not destroy data.
    assert target.database and target.database.endswith("_test"), (
        f"refusing to drop {target.database!r} — expected a name ending in '_test'"
    )

    # CREATE/DROP DATABASE can't run inside a transaction, and can't run while connected to the
    # database in question, so this connects to `postgres` in autocommit.
    admin = create_engine(_render(target.set(database="postgres")), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{target.database}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{target.database}"'))
    admin.dispose()

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    # env.py honours this instead of reading app.config, so the dev database is untouched.
    config.set_main_option("sqlalchemy.url", _render(target).replace("%", "%%"))
    command.upgrade(config, "head")

    test_engine = create_engine(_render(target))
    yield test_engine
    test_engine.dispose()


@pytest.fixture
def conn(engine: Engine) -> Iterator[Connection]:
    """A connection wrapped in a transaction that is always rolled back."""
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


@pytest.fixture
async def async_engine(engine: Engine) -> AsyncIterator[AsyncEngine]:
    """The same test database over asyncpg — the driver the application actually uses.

    Depends on `engine` only to guarantee the database exists and has been migrated.

    Function-scoped on purpose: an async engine binds to the event loop it was created on, and
    pytest-asyncio gives each test a fresh loop. A session-scoped engine would be reused across
    loops and fail. Construction is lazy, so the cost is a connection per test, not per pool.
    """
    url = _test_database_url().set(drivername="postgresql+asyncpg")
    created = create_async_engine(_render(url))
    yield created
    await created.dispose()


@pytest.fixture
async def session(async_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """An async session bound to a transaction that is always rolled back.

    Committing inside a test commits to the outer transaction's savepoint, not the database,
    so code under test is free to call `session.commit()`.
    """
    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        async_session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            # Explicit rather than relying on the default: a commit inside the test releases a
            # savepoint instead of committing the outer transaction, so route handlers can call
            # session.commit() and the test still rolls everything back.
            join_transaction_mode="create_savepoint",
        )
        try:
            yield async_session
        finally:
            await async_session.close()
            await transaction.rollback()


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """An HTTP client against the app, sharing the test's rolled-back session.

    Overriding `get_session` is what keeps API tests off the development database.
    """

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            yield http_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
async def user(session: AsyncSession) -> User:
    """A customer account, with no Google identity linked."""
    user = User(name="Test Student", email=f"student-{uuid4().hex[:12]}@example.com")
    session.add(user)
    await session.flush()
    return user


@pytest.fixture
async def other_user(session: AsyncSession) -> User:
    """A second customer account, for the tests about someone else's data."""
    other = User(name="Other Student", email=f"other-{uuid4().hex[:12]}@example.com")
    session.add(other)
    await session.flush()
    return other


@pytest.fixture
async def admin_account(session: AsyncSession) -> AdminAccount:
    """A staff account. Comped entitlements are granted by one of these."""
    admin = AdminAccount(name="Test Admin", email=f"admin-{uuid4().hex[:12]}@example.com")
    session.add(admin)
    await session.flush()
    return admin


@pytest.fixture
async def published_questionnaire(
    session: AsyncSession, admin_account: AdminAccount
) -> Questionnaire:
    """A published questionnaire. Publishing is what makes it immutable and activatable."""
    questionnaire = Questionnaire(
        nickname="Test Questionnaire",
        version="1.0",
        published_at=datetime.now(UTC),
        published_by=admin_account.id,
    )
    session.add(questionnaire)
    await session.flush()
    return questionnaire


@pytest.fixture
async def active_questionnaire(
    session: AsyncSession, published_questionnaire: Questionnaire
) -> Questionnaire:
    """The live questionnaire — the one `GET /api/questionnaire` serves."""
    await activate(
        session, published_questionnaire.id, actor_id=published_questionnaire.published_by
    )
    return published_questionnaire


@pytest.fixture
async def live(session: AsyncSession) -> None:
    """Seed a real, active questionnaire, so the app can be driven end to end.

    A blank `Questionnaire` row would be enough to satisfy the foreign key, but it has no
    questions in it — so `POST /api/responses` would return a questionnaire with nothing to
    answer, and the tests that need a real flow would be testing a mock. This seeds the
    actual spec from `tests/fixtures/`, with the career families it refers to.
    """
    await seed_career_families(
        session, CareerFamiliesSpec.from_file(SEEDS_DIR / "career-families.yaml")
    )
    spec = QuestionnaireSpec.from_file(CURRENT_QUESTIONNAIRE)
    questionnaire_id = await seed_questionnaire(session, spec, publish=True)
    await activate(session, questionnaire_id)


@pytest.fixture
async def response(
    session: AsyncSession, user: User, active_questionnaire: Questionnaire
) -> QuestionnaireResponse:
    """A started response, owned by `user`."""
    started = QuestionnaireResponse(
        user_id=user.id,
        questionnaire_id=active_questionnaire.id,
        contact_email=user.email,
    )
    session.add(started)
    await session.flush()
    return started


# --------------------------------------------------------------------- Stripe


@pytest.fixture(autouse=True)
def stripe_unconfigured(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No Stripe credentials, whatever the developer's .env says.

    Same reasoning as `locked` and `google_unconfigured`. A developer's .env may hold real
    Stripe keys, and without this a test asserting "503 when Stripe isn't configured" would
    pass on their machine and fail in CI — the exact shape of failure the project rule about
    ambient configuration exists to prevent.

    The `stripe_configured` fixture below is the opt-in for the tests that need them.
    """
    monkeypatch.setenv("STRIPE_SECRET_KEY", "")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "")
    monkeypatch.setenv("STRIPE_PRICE_ID", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def stripe_configured(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Test Stripe credentials, and the secret the tests sign webhook bodies with.

    Opt-in, not autouse: most of the suite has no business with payments, and a test that
    needs Stripe *not* to be configured shouldn't have to say so.
    """
    secret = "sk_test_not_a_real_key"
    webhook = "whsec_test_not_a_real_secret"
    monkeypatch.setenv("STRIPE_SECRET_KEY", secret)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", webhook)
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_test_not_a_real_price")
    get_settings.cache_clear()
    yield webhook
    get_settings.cache_clear()
