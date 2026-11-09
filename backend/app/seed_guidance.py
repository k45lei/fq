"""Load per-career guidance from YAML.

    uv run python -m app.seed_guidance seeds/career-guidance.yaml

Same shape as `app/seed_pay.py`: the file declares its provenance once and every career in it
inherits that. Rows upsert on (normalized_name, region, source), so authored guidance and a
future generated set can coexist, with authored preferred.

Names are stored under `normalize_career_name()`, which must stay stable — change it and every
stored row quietly stops matching.
"""

import argparse
import asyncio
from datetime import date
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_engine, get_sessionmaker
from app.models import CareerGuidance
from app.reports import KNOWN_GUIDANCE_SOURCES, normalize_career_name


class GuidanceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    study: str | None = None
    project: str | None = None
    topic: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not any([self.study, self.project, self.topic]):
            raise ValueError(f"{self.name!r} has no guidance — omit the entry instead")
        return self


class GuidanceFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    region: str = "US"
    as_of: date
    careers: list[GuidanceEntry]

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.source not in KNOWN_GUIDANCE_SOURCES:
            raise ValueError(
                f"unknown source {self.source!r}; expected one of {sorted(KNOWN_GUIDANCE_SOURCES)}"
            )

        names = [normalize_career_name(entry.name) for entry in self.careers]
        duplicates = {name for name in names if names.count(name) > 1}
        if duplicates:
            raise ValueError(f"duplicate careers after normalisation: {sorted(duplicates)}")

        return self

    @classmethod
    def from_file(cls, path: Path) -> Self:
        return cls.model_validate(yaml.safe_load(path.read_text()))


async def seed_career_guidance(session: AsyncSession, spec: GuidanceFile) -> tuple[int, int]:
    """Upsert every entry. Returns (created, updated). Does not commit."""
    existing = {
        row.normalized_name: row
        for row in (
            await session.scalars(
                select(CareerGuidance).where(
                    CareerGuidance.region == spec.region,
                    CareerGuidance.source == spec.source,
                )
            )
        ).all()
    }

    created = updated = 0
    for entry in spec.careers:
        key = normalize_career_name(entry.name)
        row = existing.get(key)

        if row is None:
            session.add(
                CareerGuidance(
                    normalized_name=key,
                    region=spec.region,
                    study=entry.study,
                    project=entry.project,
                    topic=entry.topic,
                    source=spec.source,
                    as_of=spec.as_of,
                )
            )
            created += 1
            continue

        row.study = entry.study
        row.project = entry.project
        row.topic = entry.topic
        row.as_of = spec.as_of
        updated += 1

    await session.flush()
    return created, updated


async def _main(path: Path) -> None:
    spec = GuidanceFile.from_file(path)

    async with get_sessionmaker()() as session:
        created, updated = await seed_career_guidance(session, spec)
        await session.commit()

    await get_engine().dispose()
    print(f"Career guidance [{spec.source}]: {created} created, {updated} updated")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="career guidance YAML file")
    args = parser.parse_args()

    asyncio.run(_main(args.path))


if __name__ == "__main__":
    main()
