"""CRUD + trigram search + barcode lookup."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService


async def test_create_catalog_item(db_session: AsyncSession, make_tenant) -> None:
    tenant = await make_tenant()
    service = CatalogService(CatalogRepository(db_session))
    item = await service.create_item(
        tenant_id=tenant.id,
        fields={"brand_name": "Амиксин", "inn": "тилорон", "dispensing_type": "otc"},
    )
    assert item.brand_name == "Амиксин"
    assert item.is_active is True


async def test_search_by_brand_trigram(db_session: AsyncSession, make_tenant) -> None:
    tenant = await make_tenant()
    service = CatalogService(CatalogRepository(db_session))
    await service.create_item(tenant_id=tenant.id, fields={"brand_name": "Амиксин"})
    await service.create_item(tenant_id=tenant.id, fields={"brand_name": "Парацетамол"})

    items, total, _ = await service.search(
        q="амикс", category=None, dispensing_type=None, page=1, page_size=50
    )
    names = [i.brand_name for i in items]
    assert "Амиксин" in names
    assert "Парацетамол" not in names
    assert total == 1


async def test_search_by_inn_trigram(db_session: AsyncSession, make_tenant) -> None:
    tenant = await make_tenant()
    service = CatalogService(CatalogRepository(db_session))
    await service.create_item(
        tenant_id=tenant.id, fields={"brand_name": "Brand A", "inn": "Парацетамол"}
    )
    await service.create_item(
        tenant_id=tenant.id, fields={"brand_name": "Brand B", "inn": "Ибупрофен"}
    )

    items, _, _ = await service.search(
        q="пара", category=None, dispensing_type=None, page=1, page_size=50
    )
    # search() is RLS-scoped in production, but tests run on the BYPASSRLS
    # support pool with no app.tenant_id GUC, so they also see other tenants
    # (incl. the demo seeder's «Парацетамол»). Scope the assertion to this
    # test's own tenant rather than the global result count.
    mine = [i for i in items if i.tenant_id == tenant.id]
    assert len(mine) == 1
    assert mine[0].inn == "Парацетамол"


async def test_search_by_manufacturer_trigram(db_session: AsyncSession, make_tenant) -> None:
    tenant = await make_tenant()
    service = CatalogService(CatalogRepository(db_session))
    await service.create_item(
        tenant_id=tenant.id,
        fields={"brand_name": "Brand A", "manufacturer": "Berlin-Chemie"},
    )
    await service.create_item(
        tenant_id=tenant.id,
        fields={"brand_name": "Brand B", "manufacturer": "Bayer"},
    )

    items, _, _ = await service.search(
        q="berlin", category=None, dispensing_type=None, page=1, page_size=50
    )

    mine = [item for item in items if item.tenant_id == tenant.id]
    assert len(mine) == 1
    assert mine[0].manufacturer == "Berlin-Chemie"


async def test_search_filter_by_category(db_session: AsyncSession, make_tenant) -> None:
    tenant = await make_tenant()
    service = CatalogService(CatalogRepository(db_session))
    await service.create_item(
        tenant_id=tenant.id, fields={"brand_name": "Brand A", "category": "Vitamins"}
    )
    await service.create_item(
        tenant_id=tenant.id, fields={"brand_name": "Brand B", "category": "Antibiotics"}
    )

    items, _, _ = await service.search(
        q=None,
        category="Vitamins",
        dispensing_type=None,
        page=1,
        page_size=50,
    )
    assert len(items) == 1
    assert items[0].category == "Vitamins"


async def test_barcode_lookup(db_session: AsyncSession, make_tenant) -> None:
    tenant = await make_tenant()
    service = CatalogService(CatalogRepository(db_session))
    item = await service.create_item(tenant_id=tenant.id, fields={"brand_name": "Brand X"})
    await service.add_barcode(
        tenant_id=tenant.id, catalog_id=item.id, code="4607013192829", code_type="ean13"
    )

    found = await service.find_item_by_barcode("4607013192829")
    assert found.id == item.id


async def test_unique_barcode_per_tenant(db_session: AsyncSession, make_tenant) -> None:
    tenant = await make_tenant()
    service = CatalogService(CatalogRepository(db_session))
    item1 = await service.create_item(tenant_id=tenant.id, fields={"brand_name": "X"})
    item2 = await service.create_item(tenant_id=tenant.id, fields={"brand_name": "Y"})

    await service.add_barcode(
        tenant_id=tenant.id, catalog_id=item1.id, code="111", code_type="ean13"
    )

    with pytest.raises(ConflictError):
        await service.add_barcode(
            tenant_id=tenant.id, catalog_id=item2.id, code="111", code_type="ean13"
        )


async def test_soft_delete_hides_from_search(db_session: AsyncSession, make_tenant) -> None:
    tenant = await make_tenant()
    service = CatalogService(CatalogRepository(db_session))
    item = await service.create_item(tenant_id=tenant.id, fields={"brand_name": "ToDelete"})

    await service.soft_delete_item(item.id)

    items, total, _ = await service.search(
        q="todelete", category=None, dispensing_type=None, page=1, page_size=50
    )
    assert items == []
    assert total == 0

    with pytest.raises(NotFoundError):
        await service.get_item(item.id)
