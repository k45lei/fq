"""Tests for the career family loader.

Families are the vocabulary the whole report ranks against, so the content file is worth
parsing in CI, and the upsert is worth pinning — it is the one seed that is safe to re-run,
and it must stay that way.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CareerFamily, HollandTrait
from app.seed_careers import CareerFamiliesSpec, seed_career_families

SEEDS_FILE = Path(__file__).resolve().parents[1] / "seeds" / "career-families.yaml"


def spec_from(families: list[dict]) -> CareerFamiliesSpec:
    return CareerFamiliesSpec.model_validate({"families": families})


def family(**overrides: object) -> dict:
    base = {
        "slug": "example-family",
        "name": "Example Family",
        "description": "A family.",
        "holland_code": ["realistic"],
        "example_careers": ["Someone"],
    }
    return base | overrides


# --------------------------------------------------------------------- content validation


def test_slugs_must_be_kebab_case() -> None:
    with pytest.raises(ValidationError, match="kebab-case"):
        spec_from([family(slug="Health & Medicine")])


def test_holland_code_takes_at_most_three_traits() -> None:
    with pytest.raises(ValidationError, match="one to three traits"):
        spec_from([family(holland_code=["realistic", "social", "artistic", "investigative"])])


def test_holland_code_cannot_repeat_a_trait() -> None:
    with pytest.raises(ValidationError, match="repeats a trait"):
        spec_from([family(holland_code=["social", "social"])])


def test_a_family_needs_example_careers() -> None:
    with pytest.raises(ValidationError, match="needs example_careers"):
        spec_from([family(example_careers=[])])


def test_slugs_must_be_unique_across_the_file() -> None:
    with pytest.raises(ValidationError, match="duplicate slugs"):
        spec_from([family(), family(name="Another")])


def test_the_shipped_family_file_is_valid() -> None:
    spec = CareerFamiliesSpec.from_file(SEEDS_FILE)

    assert len(spec.families) >= 10
    # Every trait should be reachable, or some users get an empty ranking.
    covered = {trait for f in spec.families for trait in f.holland_code}
    assert covered == set(HollandTrait)


# --------------------------------------------------------------------- seeding


async def test_seeding_creates_every_family(session: AsyncSession) -> None:
    spec = CareerFamiliesSpec.from_file(SEEDS_FILE)

    created, updated = await seed_career_families(session, spec)

    assert (created, updated) == (len(spec.families), 0)
    count = await session.scalar(select(func.count()).select_from(CareerFamily))
    assert count == len(spec.families)


async def test_seeding_twice_updates_rather_than_duplicating(session: AsyncSession) -> None:
    """Families are mutable reference data — re-running the seed has to be safe."""
    spec = CareerFamiliesSpec.from_file(SEEDS_FILE)
    await seed_career_families(session, spec)

    created, updated = await seed_career_families(session, spec)

    assert (created, updated) == (0, len(spec.families))
    count = await session.scalar(select(func.count()).select_from(CareerFamily))
    assert count == len(spec.families)


async def test_reseeding_applies_edited_content(session: AsyncSession) -> None:
    await seed_career_families(session, spec_from([family()]))

    await seed_career_families(
        session,
        spec_from([family(name="Renamed", example_careers=["Someone Else"])]),
    )

    stored = await session.scalar(select(CareerFamily).where(CareerFamily.slug == "example-family"))
    assert stored is not None
    assert stored.name == "Renamed"
    assert stored.example_careers == ["Someone Else"]


async def test_holland_codes_survive_the_round_trip(session: AsyncSession) -> None:
    """Ordered enum arrays are the fiddliest column in the schema — check order is preserved."""
    await seed_career_families(
        session, spec_from([family(holland_code=["social", "investigative", "realistic"])])
    )

    stored = await session.scalar(select(CareerFamily).where(CareerFamily.slug == "example-family"))
    assert stored is not None
    assert stored.holland_code == [
        HollandTrait.SOCIAL,
        HollandTrait.INVESTIGATIVE,
        HollandTrait.REALISTIC,
    ]
