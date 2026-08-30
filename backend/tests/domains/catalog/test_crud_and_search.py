"""CRUD + trigram search + barcode lookup."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.schemas import CatalogItemUpdate
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


async def test_catalog_search_accepts_an_exact_barcode(
    db_session: AsyncSession, make_tenant
) -> None:
    tenant = await make_tenant()
    service = CatalogService(CatalogRepository(db_session))
    item = await service.create_item(
        tenant_id=tenant.id, fields={"brand_name": "Barcode searchable"}
    )
    await service.add_barcode(
        tenant_id=tenant.id,
        catalog_id=item.id,
        code="4607013192999",
        code_type="ean13",
    )

    items, total, _ = await service.search(
        q="4607013192999", category=None, dispensing_type=None, page=1, page_size=50
    )

    assert total == 1
    assert [found.id for found in items] == [item.id]


async def test_picker_search_prefers_exact_name_without_full_catalog_count(
    db_session: AsyncSession, make_tenant
) -> None:
    tenant = await make_tenant()
    service = CatalogService(CatalogRepository(db_session))
    exact = await service.create_item(
        tenant_id=tenant.id,
        fields={"brand_name": "Ксарелто", "inn": "Ривароксабан"},
    )
    extended = await service.create_item(
        tenant_id=tenant.id,
        fields={"brand_name": "Ксарелто Форте", "inn": "Ривароксабан"},
    )

    items, stock = await service.search_picker(q="  Ксарелто  ", branch_id=None, limit=10)
    mine = [item for item in items if item.tenant_id == tenant.id]

    assert [item.id for item in mine[:2]] == [exact.id, extended.id]
    assert stock == {}


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


async def test_update_can_clear_nullable_fields(db_session: AsyncSession, make_tenant) -> None:
    tenant = await make_tenant()
    service = CatalogService(CatalogRepository(db_session))
    item = await service.create_item(
        tenant_id=tenant.id,
        fields={
            "brand_name": "Clear nullable",
            "inn": "value",
            "manufacturer": "Maker",
            "base_price": "12.50",
        },
    )
    payload = CatalogItemUpdate(inn=None, manufacturer=None, base_price=None)

    updated = await service.update_item(
        item.id,
        fields=payload.model_dump(exclude_unset=True),
    )

    assert updated.inn is None
    assert updated.manufacturer is None
    assert updated.base_price is None


async def test_lifecycle_search_archive_detail_and_restore(
    db_session: AsyncSession, make_tenant
) -> None:
    tenant = await make_tenant()
    service = CatalogService(CatalogRepository(db_session))
    category = f"lifecycle-{tenant.id}"
    active = await service.create_item(
        tenant_id=tenant.id,
        fields={"brand_name": "Lifecycle active", "category": category},
    )
    inactive = await service.create_item(
        tenant_id=tenant.id,
        fields={"brand_name": "Lifecycle inactive", "category": category, "is_active": False},
    )
    archived = await service.create_item(
        tenant_id=tenant.id,
        fields={"brand_name": "Lifecycle archived", "category": category},
    )
    await service.soft_delete_item(archived.id)

    active_items, _, _ = await service.search(
        q=None, category=category, dispensing_type=None, page=1, page_size=50
    )
    inactive_items, _, _ = await service.search(
        q=None,
        category=category,
        dispensing_type=None,
        page=1,
        page_size=50,
        lifecycle="inactive",
    )
    archived_items, _, _ = await service.search(
        q=None,
        category=category,
        dispensing_type=None,
        page=1,
        page_size=50,
        lifecycle="archived",
    )
    all_items, _, _ = await service.search(
        q=None,
        category=category,
        dispensing_type=None,
        page=1,
        page_size=50,
        lifecycle="all",
    )

    assert [item.id for item in active_items] == [active.id]
    assert [item.id for item in inactive_items] == [inactive.id]
    assert [item.id for item in archived_items] == [archived.id]
    assert {item.id for item in all_items} == {active.id, inactive.id, archived.id}

    detail, _barcodes = await service.get_item_with_barcodes(archived.id, include_deleted=True)
    assert detail.deleted_at is not None

    restored = await service.restore_item(archived.id)
    assert restored.deleted_at is None
    assert restored.is_active is True


async def test_search_combines_manufacturer_and_storage_filters(
    db_session: AsyncSession, make_tenant
) -> None:
    tenant = await make_tenant()
    service = CatalogService(CatalogRepository(db_session))
    category = f"filters-{tenant.id}"
    expected = await service.create_item(
        tenant_id=tenant.id,
        fields={
            "brand_name": "Cold exact",
            "category": category,
            "manufacturer": "Aurum Test Maker",
            "storage_type": "cold",
        },
    )
    await service.create_item(
        tenant_id=tenant.id,
        fields={
            "brand_name": "Normal exact",
            "category": category,
            "manufacturer": "Aurum Test Maker",
            "storage_type": "normal",
        },
    )

    items, total, _ = await service.search(
        q=None,
        category=category,
        dispensing_type=None,
        manufacturer="Aurum Test Maker",
        storage_type="cold",
        page=1,
        page_size=50,
    )

    assert total == 1
    assert [item.id for item in items] == [expected.id]
