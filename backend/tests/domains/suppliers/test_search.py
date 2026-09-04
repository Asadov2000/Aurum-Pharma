"""Supplier search filters, pagination, and explicit tenant isolation."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
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
    await service.update_supplier(
        inactive.id,
        tenant_id=tenant.id,
        fields={"is_active": False},
    )
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

    by_phone, phone_total, phone_summary = await service.search_suppliers(
        tenant_id=tenant.id,
        q="992900001111",
    )
    assert phone_total == 1
    assert by_phone[0].id == primary.id
    assert phone_summary.with_contact_count == 1

    by_legal_name, legal_name_total, _legal_summary = await service.search_suppliers(
        tenant_id=tenant.id,
        q="MEDICAL SERVICE",
        is_active=True,
    )
    assert legal_name_total == 1
    assert by_legal_name[0].id == primary.id

    inactive_items, inactive_total, inactive_summary = await service.search_suppliers(
        tenant_id=tenant.id,
        is_active=False,
    )
    assert inactive_total == 1
    assert inactive_items[0].id == inactive.id
    assert inactive_summary.all_count == 3
    assert inactive_summary.active_count == 2
    assert inactive_summary.inactive_count == 1

    first, first_total, _first_summary = await service.search_suppliers(
        tenant_id=tenant.id,
        page=1,
        page_size=2,
    )
    repeated, repeated_total, _repeated_summary = await service.search_suppliers(
        tenant_id=tenant.id,
        page=1,
        page_size=2,
    )
    assert first_total == repeated_total == 3
    assert [supplier.id for supplier in first] == [supplier.id for supplier in repeated]

    active_options = await service.search_supplier_options(
        tenant_id=tenant.id,
        q=None,
        include_inactive=False,
        selected_id=None,
        limit=10,
    )
    assert inactive.id not in {supplier.id for supplier in active_options}

    selected_option = await service.search_supplier_options(
        tenant_id=tenant.id,
        q=None,
        include_inactive=False,
        selected_id=inactive.id,
        limit=1,
    )
    assert [supplier.id for supplier in selected_option] == [inactive.id]


async def test_create_supplier_retry_is_idempotent_and_tenant_scoped(
    db_session: AsyncSession,
) -> None:
    foundation = FoundationService(FoundationRepository(db_session))
    service = SuppliersService(SuppliersRepository(db_session))
    suffix = uuid4().hex[:8]
    tenant = await foundation.create_tenant(
        payload={
            "name": f"Supplier idempotency {suffix}",
            "contact_email": f"supplier-idempotency-{suffix}@aurum.tj",
        }
    )
    other_tenant = await foundation.create_tenant(
        payload={
            "name": f"Other supplier idempotency {suffix}",
            "contact_email": f"other-supplier-idempotency-{suffix}@aurum.tj",
        }
    )
    operation_id = uuid4()
    fields = {
        "name": "Somon Medical",
        "email": "orders@somon-medical.tj",
        "notes": "Первичная поставка",
    }

    first = await service.create_supplier(
        tenant_id=tenant.id,
        operation_id=operation_id,
        fields=fields,
    )
    repeated = await service.create_supplier(
        tenant_id=tenant.id,
        operation_id=operation_id,
        fields=fields,
    )

    assert repeated.id == first.id

    await service.update_supplier(
        first.id,
        tenant_id=tenant.id,
        fields={"name": "Somon Medical Updated"},
    )
    repeated_after_update = await service.create_supplier(
        tenant_id=tenant.id,
        operation_id=operation_id,
        fields=fields,
    )
    assert repeated_after_update.id == first.id

    with pytest.raises(ConflictError, match="different data"):
        await service.create_supplier(
            tenant_id=tenant.id,
            operation_id=operation_id,
            fields={**fields, "email": "other@somon-medical.tj"},
        )

    other = await service.create_supplier(
        tenant_id=other_tenant.id,
        operation_id=operation_id,
        fields=fields,
    )
    assert other.id != first.id
