"""Load a questionnaire from a YAML file into the database.

Authoring lives in a file until there's an admin UI. The spec models below validate content
*before* anything is written, so a malformed questionnaire fails with a readable error rather
than a constraint violation.

    uv run python -m app.seed seeds/core-instrument-v2.yaml --activate

Publishing is one-way and freezes the questionnaire, so seed without `--publish` while you're
still iterating on the content.
"""

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Self
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_engine, get_sessionmaker
from app.models import (
    AdminAccount,
    AdminPermission,
    AnswerType,
    HollandTrait,
    Question,
    QuestionCategory,
    Questionnaire,
    QuestionOption,
)
from app.questionnaires import activate as activate_questionnaire

CHOICE_TYPES = {AnswerType.SINGLE_CHOICE, AnswerType.MULTI_CHOICE}

# Dev-only account used for created_by/published_by. It has no google_sub, so it cannot be
# signed in as — it exists to satisfy attribution, not to be an actual login.
SEED_ADMIN_EMAIL = "seed@futurequest.local"


class OptionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    score_value: int | None = None
    trait: HollandTrait | None = None


class QuestionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: QuestionCategory
    type: AnswerType
    prompt: str
    help_text: str | None = None
    trait: HollandTrait | None = None
    scale_min: int | None = None
    scale_max: int | None = None
    reverse_scored: bool = False
    max_selections: int | None = None
    options: list[OptionSpec] = []

    @model_validator(mode="after")
    def _check_shape(self) -> Self:
        """Mirrors the database CHECK constraints, so errors name the question, not a constraint."""
        has_options = bool(self.options)

        if self.type in CHOICE_TYPES and not has_options:
            raise ValueError(f"{self.type} question needs options: {self.prompt!r}")
        if self.type not in CHOICE_TYPES and has_options:
            raise ValueError(f"{self.type} question cannot have options: {self.prompt!r}")

        if self.type is AnswerType.SCALE:
            if self.scale_min is None or self.scale_max is None:
                raise ValueError(f"scale question needs scale_min and scale_max: {self.prompt!r}")
            if self.scale_max <= self.scale_min:
                raise ValueError(f"scale_max must exceed scale_min: {self.prompt!r}")
        else:
            if self.scale_min is not None or self.scale_max is not None:
                raise ValueError(f"only scale questions take scale bounds: {self.prompt!r}")
            if self.reverse_scored:
                raise ValueError(f"only scale questions can be reverse_scored: {self.prompt!r}")

        if self.max_selections is not None:
            if self.type is not AnswerType.MULTI_CHOICE:
                raise ValueError(f"only multi_choice takes max_selections: {self.prompt!r}")
            # Not a database constraint — it spans a table and its children — but a
            # questionnaire that lets you pick more options than exist is a content bug.
            if self.max_selections > len(self.options):
                raise ValueError(f"max_selections exceeds the option count: {self.prompt!r}")

        return self


class QuestionnaireSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nickname: str
    version: str
    questions: list[QuestionSpec]

    @classmethod
    def from_file(cls, path: Path) -> Self:
        return cls.model_validate(yaml.safe_load(path.read_text()))


async def _seed_admin(session: AsyncSession) -> AdminAccount:
    existing = await session.scalar(
        select(AdminAccount).where(AdminAccount.email == SEED_ADMIN_EMAIL)
    )
    if existing is not None:
        return existing

    admin = AdminAccount(
        name="Seed Script",
        email=SEED_ADMIN_EMAIL,
        permissions=[
            AdminPermission.AUTHOR_QUESTIONNAIRE,
            AdminPermission.PUBLISH_QUESTIONNAIRE,
        ],
    )
    session.add(admin)
    await session.flush()
    return admin


async def seed_questionnaire(
    session: AsyncSession, spec: QuestionnaireSpec, *, publish: bool, activate: bool = False
) -> UUID:
    """Create the questionnaire and its content. Returns the new questionnaire id.

    Does not commit — the caller owns the transaction, which is what lets tests roll it back.
    """
    clash = await session.scalar(
        select(Questionnaire.id).where(
            Questionnaire.nickname == spec.nickname, Questionnaire.version == spec.version
        )
    )
    if clash is not None:
        raise ValueError(
            f"{spec.nickname} {spec.version} already exists. Published questionnaires are "
            f"immutable — bump the version rather than editing in place."
        )

    actor = await _seed_admin(session)

    questionnaire = Questionnaire(nickname=spec.nickname, version=spec.version, created_by=actor.id)
    session.add(questionnaire)
    await session.flush()

    for position, question_spec in enumerate(spec.questions, start=1):
        question = Question(
            questionnaire_id=questionnaire.id,
            position=position,
            category=question_spec.category,
            answer_type=question_spec.type,
            prompt=question_spec.prompt,
            help_text=question_spec.help_text,
            trait=question_spec.trait,
            scale_min=question_spec.scale_min,
            scale_max=question_spec.scale_max,
            reverse_scored=question_spec.reverse_scored,
            max_selections=question_spec.max_selections,
            created_by=actor.id,
        )
        session.add(question)
        await session.flush()

        for option_position, option_spec in enumerate(question_spec.options, start=1):
            session.add(
                QuestionOption(
                    question_id=question.id,
                    answer_type=question_spec.type,
                    position=option_position,
                    label=option_spec.label,
                    score_value=option_spec.score_value,
                    trait=option_spec.trait,
                )
            )

    if publish:
        # Last, deliberately: the immutability triggers reject writes to a published
        # questionnaire, so every question and option must be in place before this runs.
        await session.flush()
        questionnaire.published_at = datetime.now(UTC)
        questionnaire.published_by = actor.id

    # Leave the session consistent whether or not we published. Without this the publish is
    # only a pending attribute change, invisible to any raw SQL the caller runs before commit
    # — plain text() statements don't autoflush, only ORM-enabled ones do.
    await session.flush()

    if activate:
        await activate_questionnaire(session, questionnaire.id, actor_id=actor.id)

    return questionnaire.id


async def _main(path: Path, publish: bool, activate: bool) -> None:
    spec = QuestionnaireSpec.from_file(path)

    async with get_sessionmaker()() as session:
        questionnaire_id = await seed_questionnaire(
            session, spec, publish=publish, activate=activate
        )
        await session.commit()

    await get_engine().dispose()

    state = "published and live" if activate else "published" if publish else "draft"
    print(
        f"Seeded {spec.nickname} {spec.version} as {state} "
        f"({len(spec.questions)} questions) — {questionnaire_id}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="questionnaire YAML file")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="publish immediately — irreversible, and freezes the content",
    )
    parser.add_argument(
        "--activate",
        action="store_true",
        help="serve this questionnaire to users; implies --publish",
    )
    args = parser.parse_args()

    # Activating a draft is unrepresentable, so asking for one implies the other.
    asyncio.run(_main(args.path, args.publish or args.activate, args.activate))


if __name__ == "__main__":
    main()
