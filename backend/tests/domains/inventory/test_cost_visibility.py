"""Purchase costs are a separate capability from ordinary batch visibility."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, current_user, get_db
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.inventory.repository import InventoryRepository
from app.main import app


async def test_batch_costs_are_redacted_without_explicit_permission(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    foundation = FoundationService(FoundationRepository(db_session))
    tenant = await foundation.create_tenant(
        payload={
            "name": f"Cost visibility {uuid4().hex[:8]}",
            "contact_email": f"cost-{uuid4().hex[:8]}@aurum.tj",
        }
    )
    branch = await foundation.create_branch(tenant_id=tenant.id, fields={"name": "Main"})
    item = await CatalogService(CatalogRepository(db_session)).create_item(
        tenant_id=tenant.id,
        fields={"brand_name": "Protected purchase cost"},
    )
    batch = await InventoryRepository(db_session).create_batch(
        tenant_id=tenant.id,
        branch_id=branch.id,
        catalog_id=item.id,
        expires_at=date.today() + timedelta(days=180),
        purchase_price=Decimal("4.00"),
        sale_price=Decimal("9.00"),
        qty_initial=Decimal("5.000"),
        qty_remaining=Decimal("5.000"),
    )

    actor = CurrentUser(
        user_id=uuid4(),
        tenant_id=tenant.id,
        is_developer=False,
        is_administrator=False,
        permissions={"batches.view"},
        permission_scopes={"batches.view": frozenset({branch.id})},
    )

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_user() -> CurrentUser:
        return actor

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[current_user] = _override_user
    try:
        response = await client.get("/api/v1/batches", params={"branch_id": str(branch.id)})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["items"][0]["purchase_price"] is None
        assert payload["summary"]["purchase_value"] is None

        detail = await client.get(f"/api/v1/batches/{batch.id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["purchase_price"] is None

        actor.permissions.add("batches.view_costs")
        actor.permission_scopes["batches.view_costs"] = frozenset({branch.id})
        allowed = await client.get("/api/v1/batches", params={"branch_id": str(branch.id)})
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["items"][0]["purchase_price"] == "4.00"
        assert allowed.json()["summary"]["purchase_value"] == "20.00000"
    finally:
        app.dependency_overrides.pop(current_user, None)
        app.dependency_overrides.pop(get_db, None)
