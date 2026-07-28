"""Supplier search filters, pagination, and explicit tenant isolation."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.suppliers.repository import SuppliersRepository
from app.domains.suppliers.service import SuppliersService


async def test_search_suppliers_filters_status_and_tenant(
    db_session: AsyncSession,
) -> None:
    foundation = FoundationService(FoundationRepository(db_session))
    service = SuppliersService(SuppliersRepository(db_session))
    suffix = uuid4().hex[:8]
    tenant = await foundation.create_tenant(
        payload={
            "name": f"Supplier search {suffix}",
            "contact_email": f"supplier-search-{suffix}@aurum.tj",
        }
    )
    other_tenant = await foundation.create_tenant(
        payload={
            "name": f"Other supplier search {suffix}",
            "contact_email": f"other-supplier-search-{suffix}@aurum.tj",
        }
    )
    primary = await service.create_supplier(
        tenant_id=tenant.id,
        fields={
            "name": "Med Service",
            "legal_name": "Medical Service Tajikistan",
            "inn_or_tin": "020304050",
            "contact_person": "Farid Karimov",
            "phone": "+992900001111",
            "email": "orders@med-service.tj",
            "address": "Dushanbe, Ismoili Somoni 20",
        },
    )
    inactive = await service.create_supplier(
        tenant_id=tenant.id,
        fields={"name": "Dormant Supplier", "email": "dormant@example.tj"},
    )
    await service.update_supplier(inactive.id, fields={"is_active": False})
    await service.create_supplier(
        tenant_id=tenant.id,
        fields={"name": "Local Distribution"},
    )
    await service.create_supplier(
        tenant_id=other_tenant.id,
        fields={
            "name": "Foreign Med Service",
            "phone": "+992900001111",
        },
    )

    by_phone, phone_total = await service.search_suppliers(
        tenant_id=tenant.id,
        q="992900001111",
    )
    assert phone_total == 1
    assert by_phone[0].id == primary.id

    by_legal_name, legal_name_total = await service.search_suppliers(
        tenant_id=tenant.id,
        q="MEDICAL SERVICE",
        is_active=True,
    )
    assert legal_name_total == 1
    assert by_legal_name[0].id == primary.id

    inactive_items, inactive_total = await service.search_suppliers(
        tenant_id=tenant.id,
        is_active=False,
    )
    assert inactive_total == 1
    assert inactive_items[0].id == inactive.id

    first, first_total = await service.search_suppliers(
        tenant_id=tenant.id,
        page=1,
        page_size=2,
    )
    repeated, repeated_total = await service.search_suppliers(
        tenant_id=tenant.id,
        page=1,
        page_size=2,
    )
    assert first_total == repeated_total == 3
    assert [supplier.id for supplier in first] == [supplier.id for supplier in repeated]
