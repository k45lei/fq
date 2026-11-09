"""Alembic environment.

Three things differ from the default template:

1. The database URL comes from `app.config`, not `alembic.ini`, so there is one source of
   truth and no password in a tracked file.

2. **Migrations run synchronously under psycopg, not the app's asyncpg.** asyncpg sends every
   statement as a prepared statement, and a prepared statement can hold exactly one command —
   so a migration containing a multi-statement DDL block fails with "cannot insert multiple
   commands into a prepared statement". Migrations gain nothing from async anyway: they're a
   sequential batch. The app itself still uses asyncpg; only this process swaps the driver.

3. `target_metadata` is `None` on purpose. **Migrations here are hand-written.** Autogenerate
   cannot see triggers, plpgsql functions, functional indexes or CHECK constraints, which is
   where most of this schema's integrity lives — so a generated migration would silently drop
   them. See `docs/schema.md`.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, engine_from_config, pool
from sqlalchemy.engine import make_url

from app.config import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# A caller that drives Alembic programmatically (the test suite) sets sqlalchemy.url itself.
# Only fall back to app.config when nothing has been set — otherwise tests would silently
# migrate the development database instead of their own.
if not config.get_main_option("sqlalchemy.url", default=None):
    # Swap the async driver for the sync one, keeping host/credentials/database identical.
    sync_url = make_url(get_settings().database_url.get_secret_value()).set(
        drivername="postgresql+psycopg"
    )

    # ConfigParser treats '%' as interpolation, so a password containing one breaks here.
    config.set_main_option(
        "sqlalchemy.url",
        sync_url.render_as_string(hide_password=False).replace("%", "%%"),
    )

target_metadata = None


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting — `alembic upgrade head --sql`."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        do_run_migrations(connection)

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
