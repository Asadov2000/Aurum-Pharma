"""Refresh-token rotation."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthenticationError
from app.domains.auth.repository import AuthRepository
from app.domains.auth.service import AuthService
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
        refresh_token=refresh, ip_address="127.0.0.1"
    )

    assert new_access
    assert new_refresh != refresh  # rotated
    assert expires_in == 15 * 60


async def test_refresh_rotation_invalidates_old_token(db_session: AsyncSession, make_user) -> None:
    user = await make_user(email="ref-rot@aurum.tj")
    _, refresh = await _login(db_session, user.email)

    service = AuthService(AuthRepository(db_session))
    await service.refresh(refresh_token=refresh, ip_address="127.0.0.1")

    # The same token cannot be used a second time.
    with pytest.raises(AuthenticationError):
        await service.refresh(refresh_token=refresh, ip_address="127.0.0.1")


async def test_refresh_with_garbage_token(db_session: AsyncSession) -> None:
    service = AuthService(AuthRepository(db_session))
    with pytest.raises(AuthenticationError):
        await service.refresh(refresh_token="not-a-real-token", ip_address="127.0.0.1")


async def test_logout_makes_refresh_unusable(db_session: AsyncSession, make_user) -> None:
    user = await make_user(email="lo@aurum.tj")
    _, refresh = await _login(db_session, user.email)

    service = AuthService(AuthRepository(db_session))
    await service.logout(refresh)

    with pytest.raises(AuthenticationError):
        await service.refresh(refresh_token=refresh, ip_address="127.0.0.1")


async def test_logout_is_idempotent(db_session: AsyncSession, make_user) -> None:
    user = await make_user(email="lo2@aurum.tj")
    _, refresh = await _login(db_session, user.email)

    service = AuthService(AuthRepository(db_session))
    await service.logout(refresh)
    # Calling twice is fine — no exception.
    await service.logout(refresh)
    await service.logout("definitely-not-a-token")
