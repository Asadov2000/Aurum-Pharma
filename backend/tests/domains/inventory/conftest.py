"""Fixtures for inventory tests.

`make_branch_and_item` returns a (tenant, branch, catalog_item) triple
so individual tests can focus on the batch lifecycle without rebuilding
the full chain every time.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.domains.foundation.models import Branch
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.inventory.models import Batch
from app.domains.inventory.repository import InventoryRepository


@pytest_asyncio.fixture
async def make_branch_and_item(db_session: AsyncSession):  # type: ignore[no-untyped-def]
    foundation = FoundationService(FoundationRepository(db_session))
    catalog = CatalogService(CatalogRepository(db_session))

    async def _make(
        *,
        tenant_name: str | None = None,
        item_brand: str = "Aspirin",
        expired_sale_mode: str | None = None,
    ):
        nick = uuid4().hex[:8]
        tenant = await foundation.create_tenant(
            payload={
                "name": tenant_name or f"Tenant {nick}",
                "contact_email": f"t-{nick}@aurum.tj",
            }
        )
        if expired_sale_mode is not None:
            settings = await foundation.get_settings(tenant.id)
            await foundation.repo.update_settings(settings, expired_sale_mode=expired_sale_mode)
        branch: Branch = await foundation.create_branch(
            tenant_id=tenant.id, fields={"name": "Main"}
        )
        item = await catalog.create_item(tenant_id=tenant.id, fields={"brand_name": item_brand})
        return tenant, branch, item

    return _make


@pytest_asyncio.fixture
async def make_batch(db_session: AsyncSession):  # type: ignore[no-untyped-def]
    repo = InventoryRepository(db_session)

    async def _make(
        *,
        tenant_id,
        branch_id,
        catalog_id,
        qty: Decimal | float | int = 10,
        expires_in_days: int = 90,
        purchase_price: Decimal | float | int = 5,
        sale_price: Decimal | float | int = 10,
        is_blocked: bool = False,
    ) -> Batch:
        qty_dec = Decimal(str(qty))
        return await repo.create_batch(
            tenant_id=tenant_id,
            branch_id=branch_id,
            catalog_id=catalog_id,
            expires_at=date.today() + timedelta(days=expires_in_days),
            purchase_price=Decimal(str(purchase_price)),
            sale_price=Decimal(str(sale_price)),
            qty_initial=qty_dec,
            qty_remaining=qty_dec,
            is_blocked=is_blocked,
        )

    return _make
