"""The public questionnaire flow: start, submit a section, read the report.

**Access is checked by `app/access.py`**, which accepts either a capability (this browser
started the response) or a proven identity (you signed in as the user it belongs to). The
capture email is never verified, which is why the capability route can't imply identity.

Unowned responses return **404, not 403**. A 403 would confirm the id exists, which is exactly
the information an attacker enumerating UUIDs is after.

The paid sections are gated by `entitlements`, server-side, per docs/schema.md.
"""

from io import BytesIO
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import may_access_response
from app.auth import current_user_id
from app.db import get_session
from app.email import email_report_task
from app.entitlements import has_active_entitlement
from app.models import (
    QuestionCategory,
    Questionnaire,
    QuestionnaireResponse,
    Report,
    ReportTier,
)
from app.pdf import PdfUnavailableError, render_report_pdf
from app.questionnaires import active_questionnaire_id, load_section
from app.responses import (
    COMPLETION_COLUMNS,
    NoActiveQuestionnaireError,
    ResponseNotFoundError,
    SubmissionError,
    SubmittedAnswer,
    has_completed,
    start,
    submit_section,
)
from app.schemas import (
    OptionOut,
    QuestionnaireOut,
    QuestionOut,
    ReportOut,
    StartOut,
    StartRequest,
    SubmitOut,
    SubmitRequest,
)
from app.sessions import grant_ownership

router = APIRouter(prefix="/api", tags=["public"])

SessionDep = Depends(get_session)

# The free tier.
FREE_SECTION = QuestionCategory.INTERESTS

# Presentation order when every section is unlocked.
ALL_SECTIONS = [QuestionCategory.INTERESTS, QuestionCategory.STRENGTHS, QuestionCategory.EQ]


def preview_sections() -> list[QuestionCategory]:
    """The sections a visitor with no entitlement may answer. The free one, and only it."""
    return [FREE_SECTION]


async def sections_for(
    session: AsyncSession,
    user_id: UUID,
    response_id: UUID,
) -> list[QuestionCategory]:
    """The sections this user may answer on this response.

    All of them when the gate returns a row, and the free one otherwise. The gate is the
    only thing that decides this — there is no setting that can widen it, which is the point
    of the paywall.
    """
    if await has_active_entitlement(session, user_id, response_id):
        return ALL_SECTIONS
    return [FREE_SECTION]


async def sections_for_any_response(
    session: AsyncSession,
    user_id: UUID,
) -> list[QuestionCategory]:
    """The sections this user may answer on a response that doesn't exist yet.

    Used for the questionnaire preview, which asks "what may this visitor see?" before
    there is a response to ask about. A `single_report` entitlement names one response, so
    it doesn't widen this — only a `window` one does.
    """
    # `response_id=None` asks the window-only question, which is exactly the question this
    # is: an entitlement that names a response can't widen a preview for a response that
    # doesn't exist yet.
    if await has_active_entitlement(session, user_id, None):
        return ALL_SECTIONS
    return [FREE_SECTION]


async def _questionnaire_out(
    session: AsyncSession,
    questionnaire_id: UUID,
    sections: list[QuestionCategory],
    response: QuestionnaireResponse | None = None,
) -> QuestionnaireOut:
    questionnaire = await session.get(Questionnaire, questionnaire_id)
    if questionnaire is None:  # pragma: no cover - only reachable if a row vanished mid-request
        raise HTTPException(status.HTTP_404_NOT_FOUND, "questionnaire not found")

    # Which sections are already done, so a client can skip them. A section can only be
    # submitted once, so looping over `sections` without this resubmits the free tier and is
    # refused — which is exactly what happens to a visitor who pays after finishing it.
    completed: list[QuestionCategory] = []
    if response is not None:
        for section in sections:
            if getattr(response, COMPLETION_COLUMNS[section]) is not None:
                completed.append(section)

    questions: list[QuestionOut] = []
    for section in sections:
        for question, options in await load_section(session, questionnaire_id, section):
            questions.append(
                QuestionOut(
                    id=question.id,
                    position=question.position,
                    category=question.category,
                    answer_type=question.answer_type,
                    prompt=question.prompt,
                    help_text=question.help_text,
                    scale_min=question.scale_min,
                    scale_max=question.scale_max,
                    max_selections=question.max_selections,
                    options=[OptionOut(id=option.id, label=option.label) for option in options],
                )
            )

    return QuestionnaireOut(
        id=questionnaire.id,
        nickname=questionnaire.nickname,
        version=questionnaire.version,
        sections=sections,
        completed_sections=completed,
        questions=questions,
    )


@router.get("/questionnaire", response_model=QuestionnaireOut)
async def read_questionnaire(
    request: Request,
    session: AsyncSession = SessionDep,
    response_id: UUID | None = None,
) -> QuestionnaireOut:
    """The live questionnaire's available sections, for previewing before starting.

    **`response_id` is optional, and passing it is what makes a paid visitor able to answer
    the paid sections.** The gate looks entitlements up by user, and an anonymous visitor has
    no identity — only the ownership capability, which names a response. So without a
    response to ask about, there is nothing to ask the gate, and the preview is the free
    tier.

    With a `response_id`, the response is checked for access first, and the gate is asked
    about *its owner* — the same rule the submit route uses, so the two can't disagree
    about who the visitor is.
    """
    questionnaire_id = await active_questionnaire_id(session)
    if questionnaire_id is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "no questionnaire is active yet",
        )

    sections: list[QuestionCategory]
    response: QuestionnaireResponse | None = None
    if response_id is not None:
        # Ownership first, and 404 rather than 403: a 403 would confirm the id exists.
        if not await may_access_response(session, request, response_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no response with that id")
        response = await session.get(QuestionnaireResponse, response_id)
        if response is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no response with that id")
        sections = await sections_for(session, response.user_id, response_id)
    else:
        # No response to ask about, so the only entitlement that can widen the preview is a
        # window one — and only for a signed-in visitor, because the gate looks up by user.
        user_id = current_user_id(request)
        sections = (
            await sections_for_any_response(session, user_id)
            if user_id is not None
            else preview_sections()
        )

    return await _questionnaire_out(session, questionnaire_id, sections, response)


@router.post("/responses", response_model=StartOut, status_code=status.HTTP_201_CREATED)
async def create_response(
    body: StartRequest,
    request: Request,
    http_response: Response,
    session: AsyncSession = SessionDep,
) -> StartOut:
    """Capture the visitor's details and begin an attempt.

    Returns the questions as well, so the browser needs one round trip to get started, and
    sets the cookie that makes every later request on this response possible.
    """
    try:
        response, questionnaire_id = await start(
            session,
            name=body.name,
            email=str(body.email),
            school=body.school,
            # A signed-in visitor's work belongs to their account, whatever the form carries.
            user_id=current_user_id(request),
        )
    except NoActiveQuestionnaireError as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error

    # The sections this visitor may answer. The response belongs to the account that started
    # it, so the gate is asked about that account — a signed-in visitor and the response's
    # owner are the same person here, but the gate must never be asked about a caller.
    #
    # The response was just created, so there is no entitlement naming *it* — but a visitor
    # with a window entitlement gets every section from the start. Asking about the owner
    # (rather than the caller) is what makes that work for an anonymous visitor whose
    # account has a window.
    sections = await sections_for(session, response.user_id, response.id)

    questionnaire = await _questionnaire_out(session, questionnaire_id, sections)
    await session.commit()

    grant_ownership(request, http_response, response.id)
    return StartOut(response_id=response.id, questionnaire=questionnaire)


@router.post("/responses/{response_id}/sections/{section}", response_model=SubmitOut)
async def submit(
    response_id: UUID,
    section: QuestionCategory,
    body: SubmitRequest,
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession = SessionDep,
) -> SubmitOut:
    """Submit one section and get the report it produced.

    Every section generates a report, which is the design working rather than a quirk: a
    response is scored more than once as more of it is answered, and reports are append-only.

    Generation is inline because it's deterministic and fast. Once a model is involved this
    should return immediately and the report should be produced by the job queue.
    """
    # Ownership before anything else, and 404 rather than 403: a 403 would confirm the id
    # exists, which is what someone enumerating UUIDs wants to learn.
    if not await may_access_response(session, request, response_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no response with that id")

    # The response row, for the gate and for the completion check below. A 404 rather than a
    # 403, for the same reason as the ownership check above.
    response = await session.get(QuestionnaireResponse, response_id)
    if response is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "response not found")

    # The gate is asked about the response's owner, not the caller. A visitor holding the
    # ownership cookie but not signed in gets the free tier, exactly as before.
    #
    # Asked once, and both answers derived from it: which sections may be answered, and
    # what tier the report is. Two queries could disagree — a second one a moment later
    # might see an entitlement that has just been revoked — and then the visitor would be
    # allowed to answer a section and be handed a report that says it wasn't paid for.
    entitled = await has_active_entitlement(session, response.user_id, response_id)
    sections = ALL_SECTIONS if entitled else [FREE_SECTION]
    if section not in sections:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"the {section.value} section is not included in the free report",
        )

    # The report's tier follows the same gate. `reports.tier` becomes a fact about what the
    # visitor was entitled to at scoring time, rather than a constant.
    tier = ReportTier.FULL if entitled else ReportTier.FREE

    answers = [
        SubmittedAnswer(
            question_id=answer.question_id,
            numeric=answer.numeric,
            text=answer.text,
            option_ids=tuple(answer.option_ids),
        )
        for answer in body.answers
    ]

    try:
        report = await submit_section(session, response_id, section, answers, tier=tier)
    except ResponseNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except SubmissionError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error

    report_id = report.id

    # Emailed once the sections available to this visitor are all done — so finishing the
    # free tier sends one, and completing the paid sections later sends another. Off the
    # request, because blocking a submission on an external API call would make finishing
    # the questionnaire feel broken.
    should_email = has_completed(response, sections)

    await session.commit()

    if should_email:
        background.add_task(email_report_task, report_id)

    return SubmitOut(report_id=report_id)


@router.get("/reports/{report_id}/pdf")
async def download_report(
    report_id: UUID, request: Request, session: AsyncSession = SessionDep
) -> StreamingResponse:
    """The same report, laid out for print.

    Behind the same ownership check as reading it — a downloadable copy that skipped the check
    would make the check pointless.
    """
    report = await session.scalar(select(Report).where(Report.id == report_id))
    if report is None or not await may_access_response(session, request, report.response_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "report not found")

    try:
        pdf = await render_report_pdf(report.payload, report.generated_at)
    except PdfUnavailableError as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error

    filename = f"futurequest-{report.generated_at:%Y-%m-%d}.pdf"
    return StreamingResponse(
        BytesIO(pdf),
        media_type="application/pdf",
        headers={"content-disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/reports/{report_id}", response_model=ReportOut)
async def read_report(
    report_id: UUID, request: Request, session: AsyncSession = SessionDep
) -> ReportOut:
    report = await session.scalar(select(Report).where(Report.id == report_id))

    # One 404 for both "no such report" and "not yours", so the response can't be used to
    # discover which report ids exist.
    if report is None or not await may_access_response(session, request, report.response_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "report not found")

    return ReportOut(
        id=report.id,
        response_id=report.response_id,
        tier=report.tier,
        generated_at=report.generated_at,
        payload=report.payload,
    )


@router.get("/responses/{response_id}/report", response_model=ReportOut)
async def read_latest_report(
    response_id: UUID, request: Request, session: AsyncSession = SessionDep
) -> ReportOut:
    """The newest report for a response.

    Reports are append-only: answering the paid sections later adds a *full* report rather
    than replacing the free one. So "the report" is ambiguous, and this answers it — the
    newest row for the response, which is the most complete one.

    The browser needs this because a visitor who has just paid has no report id: the
    checkout gives them a Stripe session id, not one of ours. Returning to the right report
    means asking which one it is.

    **The ordering has to be total, not just "newest".** `reports.generated_at` is
    `now()`, which in Postgres is the *transaction's* start time, not the statement's — so
    two reports written in one transaction get the same timestamp, and "newest" would
    otherwise be whichever row the planner happened to return first. The tie is broken by
    tier: `report_tier` is declared `('free', 'full')`, so `full` sorts last and therefore
    first under `DESC`. That is the ordering we want anyway — on a tie, the more complete
    report is the one to show. The id is a final tiebreak, so the result is deterministic
    even when two reports of the same tier share a timestamp.
    """
    if not await may_access_response(session, request, response_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no response with that id")

    report = await session.scalar(
        select(Report)
        .where(Report.response_id == response_id)
        .order_by(Report.generated_at.desc(), Report.tier.desc(), Report.id.desc())
        .limit(1)
    )
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no report for that response")

    return ReportOut(
        id=report.id,
        response_id=report.response_id,
        tier=report.tier,
        generated_at=report.generated_at,
        payload=report.payload,
    )
