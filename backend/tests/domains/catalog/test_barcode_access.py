"""Barcode lookup authorization for branch-scoped cashiers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, current_user, get_db
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.main import app


async def test_branch_scoped_cashier_can_scan_only_for_assigned_branch(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    foundation = FoundationService(FoundationRepository(db_session))
    catalog = CatalogService(CatalogRepository(db_session))
    suffix = uuid4().hex[:8]
    tenant = await foundation.create_tenant(
        payload={
            "name": f"Barcode cashier {suffix}",
            "contact_email": f"barcode-cashier-{suffix}@aurum.tj",
        }
    )
    branch = await foundation.create_branch(
        tenant_id=tenant.id,
        fields={"name": "Assigned branch"},
    )
    denied_branch = await foundation.create_branch(
        tenant_id=tenant.id,
        fields={"name": "Denied branch"},
    )
    item = await catalog.create_item(
        tenant_id=tenant.id,
        fields={"brand_name": "Cashier barcode item"},
    )
    code = "4607013192777"
    await catalog.add_barcode(
        tenant_id=tenant.id,
        catalog_id=item.id,
        code=code,
        code_type="ean13",
    )
    actor = CurrentUser(
        user_id=uuid4(),
        tenant_id=tenant.id,
        is_developer=False,
        is_administrator=False,
        permissions={"pos.sell"},
        permission_scopes={"pos.sell": frozenset({branch.id})},
    )

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_user() -> CurrentUser:
        return actor

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[current_user] = _override_user
    try:
        allowed = await client.get(
            f"/api/v1/catalog/by-barcode/{code}",
            params={"branch_id": str(branch.id)},
        )
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["id"] == str(item.id)

        denied = await client.get(
            f"/api/v1/catalog/by-barcode/{code}",
            params={"branch_id": str(denied_branch.id)},
        )
        assert denied.status_code == 403

        missing_scope = await client.get(f"/api/v1/catalog/by-barcode/{code}")
        assert missing_scope.status_code == 422
    finally:
        app.dependency_overrides.pop(current_user, None)
        app.dependency_overrides.pop(get_db, None)
