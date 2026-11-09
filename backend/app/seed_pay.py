"""Load career pay figures from YAML.

    uv run python -m app.seed_pay seeds/career-pay-mock.yaml

A file declares its provenance once — `source`, `region`, `currency`, `as_of` and an optional
`source_url` — and every career in it inherits that. Rows upsert on
(normalized_name, region, source), so a mock row and a real one can coexist for the same career
and the lookup prefers the real one.

Names are stored under `normalize_career_name()`, which must stay stable: change it and every
stored row silently stops matching, failing as missing pay rather than as an error.
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
from app.models import CareerPay
from app.reports import KNOWN_PAY_SOURCES, normalize_career_name


class CareerPayEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    low: int | None = None
    median: int | None = None
    high: int | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        figures = [self.low, self.median, self.high]
        if all(figure is None for figure in figures):
            raise ValueError(f"{self.name!r} has no figures — omit the entry instead")

        present = [figure for figure in figures if figure is not None]
        if any(figure <= 0 for figure in present):
            raise ValueError(f"{self.name!r} has a non-positive figure")
        if present != sorted(present):
            raise ValueError(f"{self.name!r} has figures out of order (low, median, high)")

        return self


class CareerPayFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    region: str = "US"
    currency: str = "USD"
    as_of: date
    source_url: str | None = None
    careers: list[CareerPayEntry]

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.source not in KNOWN_PAY_SOURCES:
            raise ValueError(
                f"unknown source {self.source!r}; expected one of {sorted(KNOWN_PAY_SOURCES)}"
            )
        if len(self.currency) != 3:
            raise ValueError("currency must be a three-letter code")

        names = [normalize_career_name(entry.name) for entry in self.careers]
        duplicates = {name for name in names if names.count(name) > 1}
        if duplicates:
            # They would collide on the unique key and silently overwrite each other.
            raise ValueError(f"duplicate careers after normalisation: {sorted(duplicates)}")

        return self

    @classmethod
    def from_file(cls, path: Path) -> Self:
        return cls.model_validate(yaml.safe_load(path.read_text()))


async def seed_career_pay(session: AsyncSession, spec: CareerPayFile) -> tuple[int, int]:
    """Upsert every entry. Returns (created, updated). Does not commit."""
    existing = {
        row.normalized_name: row
        for row in (
            await session.scalars(
                select(CareerPay).where(
                    CareerPay.region == spec.region, CareerPay.source == spec.source
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
                CareerPay(
                    normalized_name=key,
                    region=spec.region,
                    currency=spec.currency,
                    pay_low=entry.low,
                    pay_median=entry.median,
                    pay_high=entry.high,
                    source=spec.source,
                    source_url=spec.source_url,
                    as_of=spec.as_of,
                )
            )
            created += 1
            continue

        row.currency = spec.currency
        row.pay_low = entry.low
        row.pay_median = entry.median
        row.pay_high = entry.high
        row.source_url = spec.source_url
        row.as_of = spec.as_of
        updated += 1

    await session.flush()
    return created, updated


async def _main(path: Path) -> None:
    spec = CareerPayFile.from_file(path)

    async with get_sessionmaker()() as session:
        created, updated = await seed_career_pay(session, spec)
        await session.commit()

    await get_engine().dispose()

    caveat = "  (placeholder figures — not sourced data)" if spec.source == "mock" else ""
    print(f"Career pay [{spec.source}]: {created} created, {updated} updated{caveat}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="career pay YAML file")
    args = parser.parse_args()

    asyncio.run(_main(args.path))


if __name__ == "__main__":
    main()
