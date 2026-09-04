"""Incoming purchase costs are hidden from read-only operational roles."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, current_user, get_db
from app.domains.incoming.repository import IncomingRepository
from app.domains.incoming.service import IncomingService
from app.main import app


async def test_incoming_costs_require_an_operational_cost_capability(
    client: AsyncClient,
    db_session: AsyncSession,
    scaffold,
) -> None:
    tenant, branch, catalog_item, supplier = await scaffold()
    service = IncomingService(IncomingRepository(db_session))
    document = await service.create_document(
        tenant_id=tenant.id,
        fields={
            "branch_id": branch.id,
            "supplier_id": supplier.id,
            "document_date": date.today(),
        },
    )
    await service.add_item(
        document.id,
        fields={
            "catalog_id": catalog_item.id,
            "expires_at": date.today() + timedelta(days=365),
            "qty": Decimal("2.000"),
            "purchase_price": Decimal("4.00"),
            "sale_price": Decimal("7.00"),
        },
    )

    actor = CurrentUser(
        user_id=uuid4(),
        tenant_id=tenant.id,
        is_developer=False,
        is_administrator=False,
        permissions={"incoming.view"},
        permission_scopes={"incoming.view": frozenset({branch.id})},
    )

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_user() -> CurrentUser:
        return actor

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[current_user] = _override_user
    try:
        listing = await client.get("/api/v1/incoming")
        assert listing.status_code == 200, listing.text
        assert listing.json()["items"][0]["total_amount"] is None
        assert listing.json()["summary"]["accepted_amount"] is None

        detail = await client.get(f"/api/v1/incoming/{document.id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["total_amount"] is None
        assert detail.json()["items"][0]["purchase_price"] is None

        actor.permissions.add("batches.view_costs")
        actor.permission_scopes["batches.view_costs"] = frozenset({branch.id})
        allowed = await client.get(f"/api/v1/incoming/{document.id}")
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["total_amount"] == "8.00"
        assert allowed.json()["items"][0]["purchase_price"] == "4.00"
    finally:
        app.dependency_overrides.pop(current_user, None)
        app.dependency_overrides.pop(get_db, None)
