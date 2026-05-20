"""Fixtures for POS tests — full scaffold with batch ready for sale.

`pos_scaffold` gives a tenant in `active` status (real sales, not is_test),
a branch, a register, an item, and a single batch with qty_remaining=100.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.auth.models import AppUser
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.domains.foundation.models import Register
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.inventory.repository import InventoryRepository


@pytest_asyncio.fixture
async def pos_scaffold(db_session: AsyncSession):  # type: ignore[no-untyped-def]
    foundation = FoundationService(FoundationRepository(db_session))
    catalog = CatalogService(CatalogRepository(db_session))
    inventory_repo = InventoryRepository(db_session)

    async def _make(
        *,
        tenant_status: str = "active",
        dispensing_type: str = "otc",
        batch_qty: Decimal | float | int = 100,
        sale_price: Decimal | float | int = 10,
    ):
        nick = uuid4().hex[:8]
        tenant = await foundation.create_tenant(
            payload={
                "name": f"Tenant {nick}",
                "contact_email": f"t-{nick}@aurum.tj",
            }
        )
        if tenant_status != "setup":
            # Service moves status; refresh to see the new value.
            await foundation.update_tenant(tenant.id, fields={"status": tenant_status})
            await db_session.refresh(tenant)
        branch = await foundation.create_branch(tenant_id=tenant.id, fields={"name": "Main"})
        register: Register = await foundation.create_register(
            tenant_id=tenant.id,
            fields={"branch_id": branch.id, "name": "Касса 1"},
        )
        item = await catalog.create_item(
            tenant_id=tenant.id,
            fields={"brand_name": f"Drug {nick}", "dispensing_type": dispensing_type},
        )
        batch = await inventory_repo.create_batch(
            tenant_id=tenant.id,
            branch_id=branch.id,
            catalog_id=item.id,
            expires_at=date.today() + timedelta(days=180),
            purchase_price=Decimal("3.00"),
            sale_price=Decimal(str(sale_price)),
            qty_initial=Decimal(str(batch_qty)),
            qty_remaining=Decimal("0"),  # bumped by the incoming movement below
        )
        await inventory_repo.insert_movement(
            tenant_id=tenant.id,
            batch_id=batch.id,
            movement_type="incoming",
            qty_delta=Decimal(str(batch_qty)),
            source_table=None,
            source_id=None,
        )
        await db_session.refresh(batch)
        # cashier user
        cashier = AppUser(
            email=f"cashier-{nick}@aurum.tj",
            full_name="Cashier",
            home_tenant_id=tenant.id,
            status="active",
        )
        db_session.add(cashier)
        await db_session.flush()
        await db_session.refresh(cashier)
        return {
            "tenant": tenant,
            "branch": branch,
            "register": register,
            "item": item,
            "batch": batch,
            "cashier": cashier,
        }

    return _make
