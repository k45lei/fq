"""Tests for the entitlement gate.

The gate is the whole paywall, so what these protect is: a row that isn't valid must not
grant access, and a row that is must. The subtler cases are the ones that decide whether the
paywall holds — a revoked entitlement, an expired one, one for a different response, and one
of a kind that isn't about reports at all.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.entitlements import has_active_entitlement, has_any_active_entitlement
from app.models import AdminAccount, Entitlement, EntitlementKind, QuestionnaireResponse, User


def _validity(days: int = 30) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


async def _entitlement(
    session: AsyncSession,
    *,
    user_id: UUID,
    response_id: UUID | None,
    kind: EntitlementKind = EntitlementKind.SINGLE_REPORT,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    revoked_at: datetime | None = None,
    granted_by: UUID | None = None,
) -> Entitlement:
    """An entitlement row, for tests to vary one field at a time.

    `granted_by` is set by default because these are staff comps: `entitlements_origin`
    requires exactly one of `purchase_id` and `granted_by`, so a row with neither is
    rejected by the database.
    """
    entitlement = Entitlement(
        user_id=user_id,
        response_id=response_id,
        kind=kind,
        valid_from=valid_from,
        valid_until=valid_until,
        revoked_at=revoked_at,
        granted_by=granted_by,
    )
    session.add(entitlement)
    await session.flush()
    return entitlement


@pytest.fixture
async def comped(
    session: AsyncSession,
    user: User,
    response: QuestionnaireResponse,
    admin_account: AdminAccount,
) -> Entitlement:
    """A valid entitlement for this user on this response."""
    return await _entitlement(
        session,
        user_id=user.id,
        response_id=response.id,
        valid_until=_validity(),
        granted_by=admin_account.id,
    )


# --------------------------------------------------------------------- the happy path


async def test_a_valid_entitlement_grants_access(
    session: AsyncSession, comped: Entitlement
) -> None:
    assert await has_active_entitlement(session, comped.user_id, comped.response_id) is True


async def test_a_valid_entitlement_is_found_by_its_owner(
    session: AsyncSession, comped: Entitlement
) -> None:
    """The lookup is by user, so a row nobody holds is access to nobody."""
    assert await has_any_active_entitlement(session, comped.user_id) is True


# --------------------------------------------------------------------- refusals


async def test_no_entitlement_grants_nothing(
    session: AsyncSession, user: User, response: QuestionnaireResponse
) -> None:
    assert await has_active_entitlement(session, user.id, response.id) is False


async def test_a_revoked_entitlement_grants_nothing(
    session: AsyncSession,
    user: User,
    response: QuestionnaireResponse,
    admin_account: AdminAccount,
) -> None:
    """A refund stamps `revoked_at`, and the gate has to notice."""
    await _entitlement(
        session,
        user_id=user.id,
        response_id=response.id,
        valid_until=_validity(),
        revoked_at=datetime.now(UTC),
        granted_by=admin_account.id,
    )

    assert await has_active_entitlement(session, user.id, response.id) is False


async def test_an_expired_entitlement_grants_nothing(
    session: AsyncSession,
    user: User,
    response: QuestionnaireResponse,
    admin_account: AdminAccount,
) -> None:
    await _entitlement(
        session,
        user_id=user.id,
        response_id=response.id,
        valid_from=datetime.now(UTC) - timedelta(days=2),
        valid_until=datetime.now(UTC) - timedelta(days=1),
        granted_by=admin_account.id,
    )

    assert await has_active_entitlement(session, user.id, response.id) is False


async def test_an_entitlement_that_has_not_started_grants_nothing(
    session: AsyncSession,
    user: User,
    response: QuestionnaireResponse,
    admin_account: AdminAccount,
) -> None:
    """A window entitlement that opens tomorrow doesn't open today."""
    await _entitlement(
        session,
        user_id=user.id,
        response_id=response.id,
        valid_from=datetime.now(UTC) + timedelta(days=1),
        valid_until=_validity(2),
        granted_by=admin_account.id,
    )

    assert await has_active_entitlement(session, user.id, response.id) is False


async def test_someone_elses_entitlement_grants_nothing(
    session: AsyncSession,
    user: User,
    other_user: User,
    response: QuestionnaireResponse,
    admin_account: AdminAccount,
) -> None:
    await _entitlement(
        session,
        user_id=other_user.id,
        response_id=response.id,
        valid_until=_validity(),
        granted_by=admin_account.id,
    )

    assert await has_active_entitlement(session, user.id, response.id) is False


async def test_an_entitlement_for_another_response_grants_nothing(
    session: AsyncSession,
    user: User,
    response: QuestionnaireResponse,
    admin_account: AdminAccount,
) -> None:
    """Paying for one assessment doesn't pay for every attempt the account has made."""
    elsewhere = QuestionnaireResponse(
        user_id=user.id,
        questionnaire_id=response.questionnaire_id,
        contact_email=user.email,
    )
    session.add(elsewhere)
    await session.flush()

    await _entitlement(
        session,
        user_id=user.id,
        response_id=elsewhere.id,
        valid_until=_validity(),
        granted_by=admin_account.id,
    )

    assert await has_active_entitlement(session, user.id, response.id) is False


async def test_a_coaching_session_is_not_a_report(
    session: AsyncSession,
    user: User,
    response: QuestionnaireResponse,
    admin_account: AdminAccount,
) -> None:
    """The third kind is a booking, not access to a report.

    `coaching_session` rows name no response, so if the gate counted them it would unlock
    *every* report the user has — which is why the kind filter is in the query at all.
    """
    await _entitlement(
        session,
        user_id=user.id,
        response_id=None,
        kind=EntitlementKind.COACHING_SESSION,
        valid_until=_validity(),
        granted_by=admin_account.id,
    )

    assert await has_active_entitlement(session, user.id, response.id) is False
    assert await has_any_active_entitlement(session, user.id) is False


# --------------------------------------------------------------------- the window kind


async def test_a_window_entitlement_covers_any_response(
    session: AsyncSession,
    user: User,
    response: QuestionnaireResponse,
    admin_account: AdminAccount,
) -> None:
    """A window names no response, so it covers all of them — that's the point of it."""
    await _entitlement(
        session,
        user_id=user.id,
        response_id=None,
        kind=EntitlementKind.WINDOW,
        valid_until=_validity(),
        granted_by=admin_account.id,
    )

    assert await has_active_entitlement(session, user.id, response.id) is True
    assert await has_active_entitlement(session, user.id, None) is True
    assert await has_any_active_entitlement(session, user.id) is True


async def test_a_window_entitlement_narrows_to_windows_when_asked_for_no_response(
    session: AsyncSession,
    user: User,
    response: QuestionnaireResponse,
    admin_account: AdminAccount,
) -> None:
    """`response_id=None` asks about window access only.

    A `single_report` names one response, so it doesn't widen this question — and the
    documented query agrees: `response_id = NULL` is never true.
    """
    await _entitlement(
        session,
        user_id=user.id,
        response_id=response.id,
        valid_until=_validity(),
        granted_by=admin_account.id,
    )

    assert await has_active_entitlement(session, user.id, None) is False
    assert await has_active_entitlement(session, user.id, response.id) is True


# --------------------------------------------------------------------- boundaries


async def test_an_entitlement_is_active_up_to_its_expiry(
    session: AsyncSession,
    user: User,
    response: QuestionnaireResponse,
    admin_account: AdminAccount,
) -> None:
    """The window is half-open: `valid_from <= now < valid_until`.

    The check is `valid_until > now`, so an entitlement that expires in a second is still
    active now, and one that expired a second ago is not.
    """
    await _entitlement(
        session,
        user_id=user.id,
        response_id=response.id,
        valid_from=datetime.now(UTC) - timedelta(seconds=1),
        valid_until=datetime.now(UTC) + timedelta(seconds=1),
        granted_by=admin_account.id,
    )

    assert await has_active_entitlement(session, user.id, response.id) is True


async def test_an_entitlement_with_no_expiry_never_expires(
    session: AsyncSession,
    user: User,
    response: QuestionnaireResponse,
    admin_account: AdminAccount,
) -> None:
    """A bought report stays readable until it's refunded.

    `single_report` has no expiry by design, and `consumed_at` is never set — reading a
    report doesn't spend it.
    """
    await _entitlement(
        session,
        user_id=user.id,
        response_id=response.id,
        granted_by=admin_account.id,
    )

    assert await has_active_entitlement(session, user.id, response.id) is True
