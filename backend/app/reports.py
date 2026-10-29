"""Assemble a report from a response.

Deterministic end to end: the same answers produce byte-identical output. The narrative comes
from `app/report_copy.py` rather than a model for now, but the *shape* here is the real one —
when generation is wired up it replaces the prose, not the assembly.

Career pay is looked up, never invented. A miss omits the figure rather than guessing, so
`payload.careers[].pay` is optional and consumers must render without it.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AnswerType,
    CareerGuidance,
    CareerPay,
    HollandTrait,
    Question,
    QuestionOption,
    Report,
    ReportCareerFamily,
    ReportTier,
    ResponseAnswer,
    ResponseAnswerOption,
)
from app.report_copy import (
    DEVELOPMENT_AREAS,
    STRENGTH_CHALLENGES,
    STRENGTHS,
    TRAIT_BLURBS,
    TRAIT_LABELS,
)
from app.scoring import FamilyFit, holland_code, rank_families, score_traits

SCORING_VERSION = "deterministic-v1"

# `reqs.md` asks for five to seven challenges.
TOP_TRAIT_CHALLENGES = 3
DEVELOPMENT_CHALLENGES = 3

FAMILIES_IN_REPORT = 5
CAREERS_PER_FAMILY = 2
MAX_CAREERS = 6

DEFAULT_REGION = "US"

# Most trustworthy first. Where a career has rows from more than one source, the earliest wins —
# which is what lets placeholder figures sit alongside real ones without displacing them.
KNOWN_PAY_SOURCES = ("bls_oews", "model_research", "mock")
_PAY_PRECEDENCE = {source: rank for rank, source in enumerate(KNOWN_PAY_SOURCES)}

# Same idea for guidance: a human-written route beats a generated one.
KNOWN_GUIDANCE_SOURCES = ("authored", "model_research")
_GUIDANCE_PRECEDENCE = {source: rank for rank, source in enumerate(KNOWN_GUIDANCE_SOURCES)}

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class QAPair:
    prompt: str
    answer: str


def normalize_career_name(name: str) -> str:
    """The `career_pay` lookup key.

    **Keep this stable.** Rows are stored under whatever this returns, so changing it orphans
    every cached row — they stop matching, which fails quietly as missing pay rather than
    loudly as an error. Version it into the key rather than editing it in place.
    """
    return _WHITESPACE.sub(" ", name.strip().casefold())


async def build_transcript(session: AsyncSession, response_id: UUID) -> list[QAPair]:
    """The answered questions as question/answer text, in the order they were presented.

    This is what gets hashed, and it is what an LLM should be handed later — which is why it
    renders answers as text rather than ids.
    """
    answers = (
        await session.execute(
            select(
                Question.id,
                Question.position,
                Question.prompt,
                Question.answer_type,
                ResponseAnswer.answer_numeric,
                ResponseAnswer.answer_text,
            )
            .join(Question, Question.id == ResponseAnswer.question_id)
            .where(ResponseAnswer.response_id == response_id)
            .order_by(Question.position)
        )
    ).all()

    selections = (
        await session.execute(
            select(ResponseAnswerOption.question_id, QuestionOption.label)
            .join(QuestionOption, QuestionOption.id == ResponseAnswerOption.option_id)
            .where(ResponseAnswerOption.response_id == response_id)
        )
    ).all()

    chosen: dict[UUID, list[str]] = {}
    for question_id, label in selections:
        chosen.setdefault(question_id, []).append(label)

    transcript: list[QAPair] = []
    for question_id, _position, prompt, answer_type, numeric, free_text in answers:
        if answer_type is AnswerType.SCALE:
            answer = "" if numeric is None else str(numeric)
        elif answer_type is AnswerType.FREE_TEXT:
            answer = (free_text or "").strip()
        else:
            # Sorted: selection order carries no meaning, and an unsorted list would make the
            # hash depend on insertion order.
            answer = ", ".join(sorted(chosen.get(question_id, [])))

        transcript.append(QAPair(prompt=prompt, answer=answer))

    return transcript


def compute_input_hash(transcript: list[QAPair]) -> str:
    """Hash of the normalized question/answer text — not of row ids.

    Hashing the text means two users who gave the same answers to the same questions collide
    even across questionnaire versions where the wording didn't change.
    """
    canonical = json.dumps(
        [[pair.prompt.strip(), pair.answer.strip()] for pair in transcript],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _pay_for(session: AsyncSession, names: list[str]) -> dict[str, dict[str, Any]]:
    """Pay figures for the careers we have them for. Missing entries are simply absent."""
    if not names:
        return {}

    rows = (
        await session.scalars(
            select(CareerPay).where(
                CareerPay.normalized_name.in_([normalize_career_name(n) for n in names]),
                CareerPay.region == DEFAULT_REGION,
            )
        )
    ).all()

    best: dict[str, CareerPay] = {}
    for row in rows:
        incumbent = best.get(row.normalized_name)
        if incumbent is None or _rank(row.source) < _rank(incumbent.source):
            best[row.normalized_name] = row

    return {
        name: {
            "low": row.pay_low,
            "median": row.pay_median,
            "high": row.pay_high,
            "currency": row.currency,
            # Carried into the report so a consumer can tell sourced figures from placeholders.
            "source": row.source,
            "source_url": row.source_url,
            "as_of": row.as_of.isoformat(),
        }
        for name, row in best.items()
    }


def _rank(source: str) -> int:
    return _PAY_PRECEDENCE.get(source, len(KNOWN_PAY_SOURCES))


async def _guidance_for(session: AsyncSession, names: list[str]) -> dict[str, dict[str, Any]]:
    """What to study, build and learn, for the careers we have it for."""
    if not names:
        return {}

    rows = (
        await session.scalars(
            select(CareerGuidance).where(
                CareerGuidance.normalized_name.in_([normalize_career_name(n) for n in names]),
                CareerGuidance.region == DEFAULT_REGION,
            )
        )
    ).all()

    best: dict[str, CareerGuidance] = {}
    for row in rows:
        incumbent = best.get(row.normalized_name)
        rank = _GUIDANCE_PRECEDENCE.get(row.source, len(KNOWN_GUIDANCE_SOURCES))
        if incumbent is None or rank < _GUIDANCE_PRECEDENCE.get(
            incumbent.source, len(KNOWN_GUIDANCE_SOURCES)
        ):
            best[row.normalized_name] = row

    # Only the fields that are actually present — a null here means "we have nothing to say",
    # and an empty string in the report is worse than a missing line.
    return {
        name: {
            key: value
            for key, value in (
                ("study", row.study),
                ("project", row.project),
                ("topic", row.topic),
            )
            if value
        }
        for name, row in best.items()
    }


def _build_challenges(scores: dict[HollandTrait, float]) -> list[dict[str, str]]:
    ranked = list(scores)
    challenges = [
        {"title": title, "detail": detail}
        for trait in ranked[:TOP_TRAIT_CHALLENGES]
        for title, detail in [STRENGTH_CHALLENGES[trait]]
    ]

    # Lowest-scoring traits, weakest first, as development areas.
    for trait in list(reversed(ranked))[:DEVELOPMENT_CHALLENGES]:
        if trait in ranked[:TOP_TRAIT_CHALLENGES]:
            continue  # too few traits scored for top and bottom to be different
        title, detail = DEVELOPMENT_AREAS[trait]
        challenges.append({"title": title, "detail": detail})

    return challenges


async def _build_careers(
    session: AsyncSession, families: list[FamilyFit], code: list[HollandTrait]
) -> list[dict[str, Any]]:
    from app.models import CareerFamily

    examples = dict(
        (
            await session.execute(
                select(CareerFamily.id, CareerFamily.example_careers).where(
                    CareerFamily.id.in_([family.family_id for family in families])
                )
            )
        ).all()
    )

    traits = " and ".join(TRAIT_LABELS[trait].lower() for trait in code[:2]) or "your answers"

    careers: list[dict[str, Any]] = []
    for family in families:
        for name in examples.get(family.family_id, [])[:CAREERS_PER_FAMILY]:
            careers.append(
                {
                    "name": name,
                    "family_id": str(family.family_id),
                    "fit_score": family.fit_score,
                    "rationale": (
                        f"{family.name} work tends to suit {traits} people, which is where "
                        f"your answers pointed most strongly."
                    ),
                }
            )
    careers = careers[:MAX_CAREERS]

    names = [career["name"] for career in careers]
    pay = await _pay_for(session, names)
    guidance = await _guidance_for(session, names)

    for career in careers:
        key = normalize_career_name(career["name"])
        figure = pay.get(key)
        if figure is not None:
            career["pay"] = figure
        route = guidance.get(key)
        if route:
            career["guidance"] = route

    return careers


async def generate_report(
    session: AsyncSession, response_id: UUID, *, tier: ReportTier = ReportTier.FREE
) -> Report:
    """Score, assemble and persist a report. Appends — never overwrites an earlier one."""
    scores = await score_traits(session, response_id)
    code = holland_code(scores)
    families = await rank_families(session, scores, limit=FAMILIES_IN_REPORT)

    transcript = await build_transcript(session, response_id)

    payload: dict[str, Any] = {
        "holland_code": [trait.value for trait in code],
        "traits": {
            trait.value: {
                "score": score,
                "label": TRAIT_LABELS[trait],
                "blurb": TRAIT_BLURBS[trait],
            }
            for trait, score in scores.items()
        },
        "strengths": [
            {"name": STRENGTHS[trait][0], "evidence": STRENGTHS[trait][1]} for trait in code
        ],
        "families": [
            {
                "family_id": str(family.family_id),
                "slug": family.slug,
                "name": family.name,
                "rank": rank,
                "fit_score": family.fit_score,
            }
            for rank, family in enumerate(families, start=1)
        ],
        "careers": await _build_careers(session, families, code),
        "challenges": _build_challenges(scores),
        # Next steps used to be a single trait-derived list here. They're now attached to each
        # career instead: "study agronomy, run a growing trial, learn soil chemistry" is a
        # direction, where "start a portfolio" was a platitude.
    }

    report = Report(
        response_id=response_id,
        tier=tier,
        scoring_version=SCORING_VERSION,
        model_version=None,
        input_hash=compute_input_hash(transcript),
        payload=payload,
    )
    session.add(report)
    await session.flush()

    # The ranked families are written as rows as well as into the payload, so the family ids
    # are a real foreign key. One source feeds both, so they cannot drift.
    for rank, family in enumerate(families, start=1):
        session.add(
            ReportCareerFamily(
                report_id=report.id,
                family_id=family.family_id,
                rank=rank,
                fit_score=family.fit_score,
            )
        )
    await session.flush()

    return report
