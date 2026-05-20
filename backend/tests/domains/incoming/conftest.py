"""Fixtures for incoming/suppliers tests — share a tenant + branch + item
+ supplier scaffold so each test focuses on the flow."""

from __future__ import annotations

from uuid import uuid4

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.suppliers.repository import SuppliersRepository
from app.domains.suppliers.service import SuppliersService


@pytest_asyncio.fixture
async def scaffold(db_session: AsyncSession):  # type: ignore[no-untyped-def]
    """Returns a (tenant, branch, item, supplier) tuple."""
    foundation = FoundationService(FoundationRepository(db_session))
    catalog = CatalogService(CatalogRepository(db_session))
    suppliers = SuppliersService(SuppliersRepository(db_session))

    async def _make():
        nick = uuid4().hex[:8]
        tenant = await foundation.create_tenant(
            payload={
                "name": f"Tenant {nick}",
                "contact_email": f"t-{nick}@aurum.tj",
            }
        )
        branch = await foundation.create_branch(tenant_id=tenant.id, fields={"name": "Main"})
        item = await catalog.create_item(tenant_id=tenant.id, fields={"brand_name": "Aspirin"})
        supplier = await suppliers.create_supplier(
            tenant_id=tenant.id,
            fields={"name": f"Supplier {nick}"},
        )
        return tenant, branch, item, supplier

    return _make
