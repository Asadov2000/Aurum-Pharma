"""Refresh-token rotation."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthenticationError
from app.domains.auth.repository import AuthRepository
from app.domains.auth.service import AuthService
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.roles.models import TenantMembership
from tests.domains.auth.test_login import _seed_code


async def _login(db_session: AsyncSession, email: str) -> tuple[str, str]:
    await _seed_code(db_session, email, code="123456")
    service = AuthService(AuthRepository(db_session))
    access, refresh, _ = await service.verify_login_code(
        email=email, code="123456", password=None, ip_address="127.0.0.1"
    )
    return access, refresh


async def test_refresh_happy_path(db_session: AsyncSession, make_user) -> None:
    user = await make_user(email="ref-ok@aurum.tj")
    _, refresh = await _login(db_session, user.email)

    service = AuthService(AuthRepository(db_session))
    new_access, new_refresh, expires_in = await service.refresh(
        refresh_token=refresh,
        operation_id=uuid4(),
        ip_address="127.0.0.1",
    )

    assert new_access
    assert new_refresh != refresh  # rotated
    assert expires_in == 15 * 60


@pytest.mark.parametrize("membership_status", ["suspended", "offboarded"])
async def test_refresh_rejects_inactive_tenant_membership(
    db_session: AsyncSession,
    make_user,
    membership_status: str,
) -> None:
    tenant = await FoundationService(FoundationRepository(db_session)).create_tenant(
        payload={
            "name": f"Inactive refresh {membership_status}",
            "contact_email": f"inactive-refresh-{membership_status}@aurum.tj",
        }
    )
    user = await make_user(
        email=f"refresh-{membership_status}@aurum.tj",
        home_tenant_id=tenant.id,
    )
    membership = TenantMembership(
        tenant_id=tenant.id,
        user_id=user.id,
        full_name=user.full_name,
        status="active",
    )
    db_session.add(membership)
    await db_session.flush()
    _, refresh = await _login(db_session, user.email)
    membership.status = membership_status
    await db_session.flush()

    with pytest.raises(AuthenticationError):
        await AuthService(AuthRepository(db_session)).refresh(
            refresh_token=refresh,
            operation_id=uuid4(),
            ip_address="127.0.0.1",
        )


async def test_refresh_rotation_invalidates_old_token(db_session: AsyncSession, make_user) -> None:
    user = await make_user(email="ref-rot@aurum.tj")
    _, refresh = await _login(db_session, user.email)

    service = AuthService(AuthRepository(db_session))
    await service.refresh(
        refresh_token=refresh,
        operation_id=uuid4(),
        ip_address="127.0.0.1",
    )

    # The same token cannot be used a second time.
    with pytest.raises(AuthenticationError):
        await service.refresh(
            refresh_token=refresh,
            operation_id=uuid4(),
            ip_address="127.0.0.1",
        )


async def test_refresh_repeats_same_rotation_after_lost_response(
    db_session: AsyncSession,
    make_user,
) -> None:
    user = await make_user(email="ref-retry@aurum.tj")
    _, refresh = await _login(db_session, user.email)
    operation_id = uuid4()
    service = AuthService(AuthRepository(db_session))

    _, first_successor, _ = await service.refresh(
        refresh_token=refresh,
        operation_id=operation_id,
        ip_address="127.0.0.1",
    )
    _, retried_successor, _ = await service.refresh(
        refresh_token=refresh,
        operation_id=operation_id,
        ip_address="127.0.0.1",
    )

    assert retried_successor == first_successor


async def test_refresh_reuses_cookie_already_set_by_same_operation(
    db_session: AsyncSession,
    make_user,
) -> None:
    user = await make_user(email="ref-cookie-retry@aurum.tj")
    _, refresh = await _login(db_session, user.email)
    operation_id = uuid4()
    service = AuthService(AuthRepository(db_session))

    _, successor, _ = await service.refresh(
        refresh_token=refresh,
        operation_id=operation_id,
        ip_address="127.0.0.1",
    )
    _, retried_successor, _ = await service.refresh(
        refresh_token=successor,
        operation_id=operation_id,
        ip_address="127.0.0.1",
    )

    assert retried_successor == successor


async def test_refresh_with_garbage_token(db_session: AsyncSession) -> None:
    service = AuthService(AuthRepository(db_session))
    with pytest.raises(AuthenticationError):
        await service.refresh(
            refresh_token="not-a-real-token",
            operation_id=uuid4(),
            ip_address="127.0.0.1",
        )


async def test_logout_makes_refresh_unusable(db_session: AsyncSession, make_user) -> None:
    user = await make_user(email="lo@aurum.tj")
    _, refresh = await _login(db_session, user.email)

    service = AuthService(AuthRepository(db_session))
    await service.logout(refresh)

    with pytest.raises(AuthenticationError):
        await service.refresh(
            refresh_token=refresh,
            operation_id=uuid4(),
            ip_address="127.0.0.1",
        )


async def test_logout_revokes_successor_after_lost_refresh_response(
    db_session: AsyncSession,
    make_user,
) -> None:
    user = await make_user(email="lo-lost-refresh@aurum.tj")
    _, refresh = await _login(db_session, user.email)
    operation_id = uuid4()
    service = AuthService(AuthRepository(db_session))
    _, successor, _ = await service.refresh(
        refresh_token=refresh,
        operation_id=operation_id,
        ip_address="127.0.0.1",
    )

    await service.logout(refresh, operation_id=operation_id)

    with pytest.raises(AuthenticationError):
        await service.refresh(
            refresh_token=successor,
            operation_id=uuid4(),
            ip_address="127.0.0.1",
        )


async def test_logout_is_idempotent(db_session: AsyncSession, make_user) -> None:
    user = await make_user(email="lo2@aurum.tj")
    _, refresh = await _login(db_session, user.email)

    service = AuthService(AuthRepository(db_session))
    await service.logout(refresh)
    # Calling twice is fine — no exception.
    await service.logout(refresh)
    await service.logout("definitely-not-a-token")
