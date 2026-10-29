"""Request and response bodies for the public API.

Note what `QuestionOut` leaves out: `trait`, `score_value` and `reverse_scored` never reach the
browser. They're scoring internals, and exposing them would let anyone work out which answers
produce which result.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import AnswerType, QuestionCategory, ReportTier


class OptionOut(BaseModel):
    id: UUID
    label: str


class QuestionOut(BaseModel):
    id: UUID
    position: int
    category: QuestionCategory
    answer_type: AnswerType
    prompt: str
    help_text: str | None
    scale_min: int | None
    scale_max: int | None
    max_selections: int | None
    options: list[OptionOut]


class QuestionnaireOut(BaseModel):
    id: UUID
    nickname: str
    version: str
    # Which sections the caller may answer, in presentation order. Normally just the free one;
    # all three when the user has an active entitlement. Each question carries its own `category`,
    # so a client can group without a second lookup.
    sections: list[QuestionCategory]
    # Which sections this response has *already* had submitted. A section can only be
    # submitted once, so a client that loops over `sections` needs to know which to skip —
    # otherwise a visitor who paid after finishing the free tier resubmits it and is refused.
    # Empty for a response that has just started.
    completed_sections: list[QuestionCategory] = []
    questions: list[QuestionOut]


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    school: str | None = Field(default=None, max_length=200)


class StartOut(BaseModel):
    response_id: UUID
    questionnaire: QuestionnaireOut


class AnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: UUID
    # Exactly one of these is meaningful, decided by the question's answer_type. Validated
    # server-side against the question rather than here, so the error can name the question.
    numeric: int | None = None
    text: str | None = None
    option_ids: list[UUID] = Field(default_factory=list)


class SubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: list[AnswerIn] = Field(min_length=1)


class SubmitOut(BaseModel):
    report_id: UUID


class TraitOut(BaseModel):
    score: float = Field(ge=0, le=1)
    label: str
    blurb: str


class PayOut(BaseModel):
    """Present only when `career_pay` had a figure. A miss omits this object entirely."""

    low: int | None
    median: int | None
    high: int | None
    currency: str
    # 'bls_oews' | 'model_research' | 'mock'. Carried so consumers can distinguish sourced
    # figures from placeholders rather than presenting both with equal authority.
    source: str
    source_url: str | None
    as_of: str


class StrengthOut(BaseModel):
    name: str
    evidence: str


class RankedFamilyOut(BaseModel):
    family_id: UUID
    slug: str
    name: str
    rank: int
    fit_score: float


class GuidanceOut(BaseModel):
    """What to do about this career. Each field is absent when there's nothing to say — an
    empty string in a report is worse than a missing line."""

    study: str | None = None
    project: str | None = None
    topic: str | None = None


class CareerOut(BaseModel):
    name: str
    family_id: UUID
    fit_score: float
    rationale: str
    pay: PayOut | None = None
    guidance: GuidanceOut | None = None


class ChallengeOut(BaseModel):
    title: str
    detail: str


class ReportPayloadOut(BaseModel):
    """The rendered report.

    Typed here rather than left as a free-form object so that OpenAPI describes it — it is the
    most important part of the contract, and an opaque `object` in the schema would leave every
    consumer guessing. Keep in step with what `app/reports.py` assembles; the API tests read a
    generated report back through this model, so a mismatch fails there.
    """

    holland_code: list[str]
    traits: dict[str, TraitOut]
    strengths: list[StrengthOut]
    families: list[RankedFamilyOut]
    careers: list[CareerOut]
    challenges: list[ChallengeOut]


class PastReportOut(BaseModel):
    """A report in someone's history — enough to list and link to, not the whole payload."""

    id: UUID
    generated_at: datetime
    tier: ReportTier


class MeOut(BaseModel):
    signed_in: bool
    name: str | None = None
    email: str | None = None
    reports: list[PastReportOut] = Field(default_factory=list)


class ReportOut(BaseModel):
    id: UUID
    response_id: UUID
    tier: ReportTier
    generated_at: datetime
    payload: ReportPayloadOut


class CheckoutRequest(BaseModel):
    """What the visitor sends when they ask to pay.

    A discount code is optional and validated against the `discount_codes` table before
    Stripe is involved — a wrong code should be reported while the visitor is still on the
    page, not after they've been handed to a Stripe checkout they can't complete.
    """

    model_config = ConfigDict(extra="forbid")

    discount_code: str | None = Field(default=None, max_length=100)


class CheckoutOut(BaseModel):
    """Where to send the browser, and what to call the session later.

    Deliberately not the Stripe session object. The frontend needs one thing — a URL to
    navigate to — and returning Stripe's serialisation would pin this contract to a third
    party's shape and leak their internals into it.
    """

    url: str
    session_id: str


class PricingOut(BaseModel):
    """What the report costs.

    Served so the paywall can state the price *before* the visitor commits to a checkout.
    The number comes from the server, never from the frontend — the same reason pay figures
    come from the `career_pay` lookup and are never fabricated.
    """

    amount_cents: int
    currency: str


class CheckoutStatusOut(BaseModel):
    """Whether a checkout has resulted in access, after the browser comes back.

    Stripe redirects the browser back immediately, and the webhook that grants access may
    land a second or two later — or not at all, if the delivery failed. This endpoint is
    checked on return, and it verifies the session with Stripe rather than assuming, so a
    paying visitor is never told they have no access because a background delivery was
    slow.
    """

    session_id: str
    paid: bool
    entitled: bool
    response_id: UUID | None = None
