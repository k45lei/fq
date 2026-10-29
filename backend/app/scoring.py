"""Deterministic scoring: Holland trait scores from a response, and family ranking from those.

This is the layer `docs/schema.md` calls "augmenting" — it doesn't write prose, it produces the
structured facts a report is built on. Everything here is a pure function of the stored answers,
which is what makes identical inputs produce identical output. When an LLM is wired up it should
receive this as context rather than recomputing it.

Two normalisations worth knowing about:

* A trait's score is *earned over available* — how much of the signal that was actually on offer
  for that trait the answers picked up. A trait nothing asked about is absent rather than zero,
  because "we didn't ask" and "you scored nothing" are different claims.
* Ties break on the enum's declaration order, never on whatever order the database returned
  rows in. Stable output matters more here than the tie-break being meaningful.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AnswerType,
    CareerFamily,
    HollandTrait,
    Question,
    QuestionOption,
    ResponseAnswer,
    ResponseAnswerOption,
)

# Positional weights for a family's Holland code: the first trait is what the family is most
# characteristically about, so it counts for most.
CODE_POSITION_WEIGHTS = (3.0, 2.0, 1.0)

TRAIT_ORDER = list(HollandTrait)


@dataclass(frozen=True)
class FamilyFit:
    family_id: UUID
    slug: str
    name: str
    fit_score: float


def _sort_key(trait: HollandTrait, score: float) -> tuple[float, int]:
    return (-score, TRAIT_ORDER.index(trait))


async def score_traits(session: AsyncSession, response_id: UUID) -> dict[HollandTrait, float]:
    """Trait scores in [0, 1], highest first. Traits with no signal are omitted entirely."""
    earned: dict[HollandTrait, float] = {}
    available: dict[HollandTrait, float] = {}

    # Scale questions: the normalised answer contributes to the question's trait.
    scale_rows = await session.execute(
        select(
            Question.trait,
            Question.scale_min,
            Question.scale_max,
            Question.reverse_scored,
            ResponseAnswer.answer_numeric,
        )
        .join(Question, Question.id == ResponseAnswer.question_id)
        .where(
            ResponseAnswer.response_id == response_id,
            Question.answer_type == AnswerType.SCALE,
            Question.trait.is_not(None),
            ResponseAnswer.answer_numeric.is_not(None),
        )
    )
    for trait, scale_min, scale_max, reverse_scored, value in scale_rows:
        span = scale_max - scale_min
        if span <= 0:  # pragma: no cover - a CHECK constraint already forbids this
            continue
        normalised = (value - scale_min) / span
        contribution = 1.0 - normalised if reverse_scored else normalised
        earned[trait] = earned.get(trait, 0.0) + contribution
        available[trait] = available.get(trait, 0.0) + 1.0

    # Choice questions: every option carrying a trait is a point that was on offer...
    offered_rows = await session.execute(
        select(QuestionOption.trait)
        .join(ResponseAnswer, ResponseAnswer.question_id == QuestionOption.question_id)
        .where(
            ResponseAnswer.response_id == response_id,
            QuestionOption.trait.is_not(None),
        )
    )
    for (trait,) in offered_rows:
        available[trait] = available.get(trait, 0.0) + 1.0

    # ...and every option actually selected is a point earned.
    selected_rows = await session.execute(
        select(QuestionOption.trait)
        .join(ResponseAnswerOption, ResponseAnswerOption.option_id == QuestionOption.id)
        .where(
            ResponseAnswerOption.response_id == response_id,
            QuestionOption.trait.is_not(None),
        )
    )
    for (trait,) in selected_rows:
        earned[trait] = earned.get(trait, 0.0) + 1.0

    scores = {
        trait: round(earned.get(trait, 0.0) / total, 3)
        for trait, total in available.items()
        if total > 0
    }
    return dict(sorted(scores.items(), key=lambda item: _sort_key(*item)))


def holland_code(scores: dict[HollandTrait, float], length: int = 3) -> list[HollandTrait]:
    """The top traits, strongest first. Shorter than `length` if there isn't enough signal."""
    ranked = sorted(scores.items(), key=lambda item: _sort_key(*item))
    return [trait for trait, score in ranked[:length] if score > 0]


async def rank_families(
    session: AsyncSession, scores: dict[HollandTrait, float], limit: int = 5
) -> list[FamilyFit]:
    """Active families, best fit first.

    A family's fit is the weighted average of the user's scores for the traits in its code,
    so a family whose leading trait the user scored highly outranks one that merely mentions it.
    """
    families = (
        await session.scalars(select(CareerFamily).where(CareerFamily.is_active.is_(True)))
    ).all()

    fits: list[FamilyFit] = []
    for family in families:
        weights = CODE_POSITION_WEIGHTS[: len(family.holland_code)]
        if not weights:
            continue

        weighted = sum(
            scores.get(trait, 0.0) * weight
            for trait, weight in zip(family.holland_code, weights, strict=False)
        )
        fits.append(
            FamilyFit(
                family_id=family.id,
                slug=family.slug,
                name=family.name,
                fit_score=round(weighted / sum(weights), 3),
            )
        )

    # Slug as the tie-break so equal fits come out in a stable order.
    fits.sort(key=lambda fit: (-fit.fit_score, fit.slug))
    return fits[:limit]
