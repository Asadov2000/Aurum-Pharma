"""Global audit must use the developer-only administrative data path."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.domains.auth.models import AppUser
from app.main import app
from tests.auth_helpers import create_support_access_token


async def test_global_audit_is_only_available_in_admin_namespace(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    developer = AppUser(
        email=f"global-audit-developer-{uuid4().hex}@aurum.tj",
        full_name="Aurum developer",
        status="active",
        is_developer=True,
        is_administrator=False,
    )
    db_session.add(developer)
    await db_session.flush()
    token = await create_support_access_token(
        db_session,
        developer,
    )

    app.dependency_overrides[get_db] = _override
    try:
        response = await client.get(
            "/api/v1/admin/audit/global",
            headers={"Authorization": f"Bearer {token}"},
        )
        legacy_response = await client.get(
            "/api/v1/audit/global",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200, response.text
        assert isinstance(response.json()["items"], list)
        assert legacy_response.status_code == 404
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_global_audit_rejects_aurum_administrator(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    administrator = AppUser(
        email=f"global-audit-admin-{uuid4().hex}@aurum.tj",
        full_name="Aurum administrator",
        status="active",
        is_developer=False,
        is_administrator=True,
    )
    db_session.add(administrator)
    await db_session.flush()
    token = await create_support_access_token(
        db_session,
        administrator,
    )

    app.dependency_overrides[get_db] = _override
    try:
        response = await client.get(
            "/api/v1/admin/audit/global",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_db, None)
