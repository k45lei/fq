"""Load the career family vocabulary from YAML.

Families are the closed set the report ranks and — once there's an LLM in the loop — the
vocabulary it must choose from. Individual careers are deliberately not modelled; see
`docs/schema.md`.

    uv run python -m app.seed_careers seeds/career-families.yaml

Unlike questionnaires, families are mutable reference data with no immutability triggers, so
this upserts on `slug` and is safe to re-run. Families missing from the file are left alone
rather than deleted — historical reports reference them. Retire one by setting `is_active: false`.
"""

import argparse
import asyncio
import re
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_engine, get_sessionmaker
from app.models import CareerFamily, HollandTrait

SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class CareerFamilySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    description: str
    holland_code: list[HollandTrait]
    example_careers: list[str]
    is_active: bool = True

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not SLUG_PATTERN.match(self.slug):
            raise ValueError(f"slug must be lowercase kebab-case: {self.slug!r}")

        if not 1 <= len(self.holland_code) <= 3:
            raise ValueError(f"holland_code takes one to three traits: {self.slug!r}")
        if len(set(self.holland_code)) != len(self.holland_code):
            raise ValueError(f"holland_code repeats a trait: {self.slug!r}")

        if not self.example_careers:
            raise ValueError(
                f"{self.slug!r} needs example_careers — they anchor the model and stand in "
                f"for suggestions until one is wired up"
            )
        return self


class CareerFamiliesSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    families: list[CareerFamilySpec]

    @model_validator(mode="after")
    def _unique_slugs(self) -> Self:
        slugs = [family.slug for family in self.families]
        duplicates = {slug for slug in slugs if slugs.count(slug) > 1}
        if duplicates:
            raise ValueError(f"duplicate slugs: {sorted(duplicates)}")
        return self

    @classmethod
    def from_file(cls, path: Path) -> Self:
        return cls.model_validate(yaml.safe_load(path.read_text()))


async def seed_career_families(session: AsyncSession, spec: CareerFamiliesSpec) -> tuple[int, int]:
    """Upsert every family in the spec. Returns (created, updated). Does not commit."""
    existing = {
        family.slug: family for family in (await session.scalars(select(CareerFamily))).all()
    }

    created = updated = 0
    for family_spec in spec.families:
        family = existing.get(family_spec.slug)

        if family is None:
            session.add(
                CareerFamily(
                    slug=family_spec.slug,
                    name=family_spec.name,
                    description=family_spec.description,
                    holland_code=family_spec.holland_code,
                    example_careers=family_spec.example_careers,
                    is_active=family_spec.is_active,
                )
            )
            created += 1
            continue

        family.name = family_spec.name
        family.description = family_spec.description
        family.holland_code = family_spec.holland_code
        family.example_careers = family_spec.example_careers
        family.is_active = family_spec.is_active
        updated += 1

    await session.flush()
    return created, updated


async def _main(path: Path) -> None:
    spec = CareerFamiliesSpec.from_file(path)

    async with get_sessionmaker()() as session:
        created, updated = await seed_career_families(session, spec)
        await session.commit()

    await get_engine().dispose()
    print(f"Career families: {created} created, {updated} updated")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="career families YAML file")
    args = parser.parse_args()

    asyncio.run(_main(args.path))


if __name__ == "__main__":
    main()
