"""Authorization gates: sensitive reads (purchase prices, billing) require
reports.view — a seller is refused, an administrator is allowed."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.security import create_access_token
from app.domains.auth.models import AppUser
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.main import app

GATED_GET_PATHS = ["/api/v1/batches", "/api/v1/billing/invoices"]


@pytest.mark.parametrize("path", GATED_GET_PATHS)
async def test_sensitive_reads_require_reports_view(
    db_session: AsyncSession, client: AsyncClient, path: str
) -> None:
    # Share the test's SAVEPOINT session with the app for this request.
    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        foundation = FoundationService(FoundationRepository(db_session))
        nick = uuid4().hex[:8]
        tenant = await foundation.create_tenant(
            payload={"name": f"Sec {nick}", "contact_email": f"s-{nick}@aurum.tj"}
        )
        await foundation.update_tenant(tenant.id, fields={"status": "active"})

        seller = AppUser(
            email=f"seller-{nick}@aurum.tj",
            full_name="Seller",
            home_tenant_id=tenant.id,
            status="active",
        )
        admin = AppUser(
            email=f"admin-{nick}@aurum.tj",
            full_name="Admin",
            home_tenant_id=tenant.id,
            is_administrator=True,
            status="active",
        )
        db_session.add_all([seller, admin])
        await db_session.flush()
        await db_session.refresh(seller)
        await db_session.refresh(admin)

        seller_token = create_access_token(
            seller.id, tenant_id=tenant.id, is_developer=False, is_administrator=False
        )
        admin_token = create_access_token(
            admin.id, tenant_id=tenant.id, is_developer=False, is_administrator=True
        )

        seller_resp = await client.get(path, headers={"Authorization": f"Bearer {seller_token}"})
        assert seller_resp.status_code == 403, path

        admin_resp = await client.get(path, headers={"Authorization": f"Bearer {admin_token}"})
        assert admin_resp.status_code == 200, path
    finally:
        app.dependency_overrides.pop(get_db, None)
