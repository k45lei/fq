"""Smoke test for the application and the async database path.

Everything else in this suite runs over psycopg, the driver Alembic uses. This is the only test
that exercises asyncpg — the driver the application itself talks to Postgres with — so it is
also the only thing standing between "migrations work" and "the app can reach the database".
"""

from httpx import AsyncClient


async def test_health_reports_ok(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_health_round_trips_to_the_database(client: AsyncClient) -> None:
    """The endpoint issues SELECT 1; a failure here is a broken async connection, not routing."""
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["environment"] == "development"
