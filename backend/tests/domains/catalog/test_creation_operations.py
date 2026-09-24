"""Atomic catalog creation, immutable intent and authorization on real PostgreSQL."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from decimal import Decimal
from uuid import UUID, uuid1, uuid4

import pytest
from httpx import AsyncClient
from pydantic import ValidationError as SchemaValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, current_user, get_db
from app.core.errors import ConflictError
from app.domains.auth.models import AppUser
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.schemas import BarcodeCreate, CatalogItemCreate, CatalogItemRead
from app.domains.catalog.service import CatalogService
from app.domains.foundation.models import Tenant
from app.main import app

type TenantFactory = Callable[[], Awaitable[Tenant]]


async def _counts(session: AsyncSession, tenant_id: UUID) -> tuple[int, int, int]:
    row = (
        await session.execute(
            text(
                "SELECT (SELECT count(*) FROM tenant_catalog WHERE tenant_id=:tenant), "
                "(SELECT count(*) FROM barcode WHERE tenant_id=:tenant), "
                "(SELECT count(*) FROM audit_log WHERE tenant_id=:tenant "
                "AND table_name IN ('tenant_catalog','barcode'))"
            ),
            {"tenant": tenant_id},
        )
    ).one()
    return int(row[0]), int(row[1]), int(row[2])


@pytest.mark.parametrize("barcode", [None, BarcodeCreate(code=" 4607013192829 ")])
async def test_lost_reply_retry_returns_same_item_without_audit_write(
    db_session: AsyncSession, make_tenant: TenantFactory, barcode: BarcodeCreate | None
) -> None:
    tenant = await make_tenant()
    service = CatalogService(CatalogRepository(db_session))
    operation_id = uuid4()
    item = await service.create_item(
        tenant_id=tenant.id,
        fields={"brand_name": "  New medicine ", "base_price": Decimal("1")},
        operation_id=operation_id,
        initial_barcode=barcode,
    )
    item_id = item.id
    before = await _counts(db_session, tenant.id)
    db_session.expunge_all()
    replay = await service.create_item(
        tenant_id=tenant.id,
        fields={
            "brand_name": "New medicine",
            "inn": None,
            "base_price": Decimal("1.00"),
            "dispensing_type": "otc",
            "storage_type": "normal",
        },
        operation_id=operation_id,
        initial_barcode=barcode,
    )
    assert replay.id == item_id and replay.operation_id == operation_id
    assert await _counts(db_session, tenant.id) == before
    assert before[0:2] == (1, int(barcode is not None))
    public = CatalogItemRead.model_validate(replay).model_dump()
    assert "operation_id" not in public and "create_request_fingerprint" not in public


async def test_original_intent_survives_later_card_and_barcode_edits(
    db_session: AsyncSession, make_tenant: TenantFactory
) -> None:
    tenant = await make_tenant()
    service = CatalogService(CatalogRepository(db_session))
    operation_id = uuid4()
    initial = BarcodeCreate(code="4607013192829")
    item = await service.create_item(
        tenant_id=tenant.id,
        fields={"brand_name": "Original"},
        operation_id=operation_id,
        initial_barcode=initial,
    )
    item_id = item.id
    await db_session.execute(
        text("UPDATE tenant_catalog SET brand_name='Edited' WHERE id=:id"), {"id": item_id}
    )
    await db_session.execute(
        text("DELETE FROM barcode WHERE tenant_id=:tenant AND catalog_id=:id"),
        {"tenant": tenant.id, "id": item_id},
    )
    before = await _counts(db_session, tenant.id)
    replay = await service.create_item(
        tenant_id=tenant.id,
        fields={"brand_name": "Original"},
        operation_id=operation_id,
        initial_barcode=initial,
    )
    assert replay.id == item_id and replay.brand_name == "Edited"
    assert await _counts(db_session, tenant.id) == before
    assert before[1] == 0
    for fields, barcode in (
        ({"brand_name": "Edited"}, initial),
        ({"brand_name": "Original"}, None),
    ):
        with pytest.raises(ConflictError) as error:
            await service.create_item(
                tenant_id=tenant.id,
                fields=fields,
                operation_id=operation_id,
                initial_barcode=barcode,
            )
        assert error.value.details == {"reason": "operation_payload_mismatch"}


@pytest.mark.parametrize("archived", [False, True])
async def test_unavailable_created_item_is_never_recreated(
    db_session: AsyncSession, make_tenant: TenantFactory, archived: bool
) -> None:
    tenant = await make_tenant()
    service = CatalogService(CatalogRepository(db_session))
    operation_id = uuid4()
    item = await service.create_item(
        tenant_id=tenant.id, fields={"brand_name": "Original"}, operation_id=operation_id
    )
    if archived:
        await service.soft_delete_item(item.id)
    else:
        await service.update_item(item.id, fields={"is_active": False})
    before = await _counts(db_session, tenant.id)
    with pytest.raises(ConflictError) as error:
        await service.create_item(
            tenant_id=tenant.id, fields={"brand_name": "Original"}, operation_id=operation_id
        )
    assert error.value.details == {"reason": "created_item_unavailable"}
    assert await _counts(db_session, tenant.id) == before


async def test_barcode_conflict_rolls_back_card_and_audit_and_allows_explicit_correction(
    db_session: AsyncSession, make_tenant: TenantFactory
) -> None:
    tenant = await make_tenant()
    service = CatalogService(CatalogRepository(db_session))
    barcode = BarcodeCreate(code="4607013192829")
    existing = await service.create_item(
        tenant_id=tenant.id,
        fields={"brand_name": "Existing"},
        operation_id=uuid4(),
        initial_barcode=barcode,
    )
    operation_id = uuid4()
    before = await _counts(db_session, tenant.id)
    with pytest.raises(ConflictError) as error:
        await service.create_item(
            tenant_id=tenant.id,
            fields={"brand_name": "New"},
            operation_id=operation_id,
            initial_barcode=barcode,
        )
    assert error.value.details == {"reason": "barcode_conflict"}
    assert await _counts(db_session, tenant.id) == before
    found = await service.find_item_by_barcode(barcode.code, tenant_id=tenant.id)
    assert found.id == existing.id
    corrected = await service.create_item(
        tenant_id=tenant.id,
        fields={"brand_name": "New"},
        operation_id=operation_id,
        initial_barcode=BarcodeCreate(code="4607013192888"),
    )
    assert corrected.id != existing.id


PRODUCT_IDENTITY = {
    "brand_name": "Aspirin Cardio",
    "manufacturer": "Bayer",
    "form": "tablets",
    "dosage": "100 mg",
    "pack_size": "28",
}


async def test_manual_create_requires_decision_for_complete_identity_and_preserves_retry(
    db_session: AsyncSession, make_tenant: TenantFactory
) -> None:
    tenant = await make_tenant()
    service = CatalogService(CatalogRepository(db_session))
    original = await service.create_item(tenant_id=tenant.id, fields=PRODUCT_IDENTITY)
    before = await _counts(db_session, tenant.id)
    operation_id = uuid4()
    with pytest.raises(ConflictError) as error:
        await service.create_item(
            tenant_id=tenant.id,
            fields={key: f"  {value.upper()}  " for key, value in PRODUCT_IDENTITY.items()},
            operation_id=operation_id,
        )
    assert error.value.details == {
        "reason": "catalog_identity_match",
        "catalog_id": str(original.id),
    }
    assert await _counts(db_session, tenant.id) == before
    distinct = await service.create_item(
        tenant_id=tenant.id,
        fields=PRODUCT_IDENTITY,
        operation_id=operation_id,
        confirm_distinct_item=True,
    )
    replay = await service.create_item(
        tenant_id=tenant.id,
        fields=PRODUCT_IDENTITY,
        operation_id=operation_id,
        confirm_distinct_item=True,
    )
    assert distinct.id != original.id and replay.id == distinct.id
    with pytest.raises(ConflictError) as mismatch:
        await service.create_item(
            tenant_id=tenant.id, fields=PRODUCT_IDENTITY, operation_id=operation_id
        )
    assert mismatch.value.details == {"reason": "operation_payload_mismatch"}


async def test_incomplete_or_different_identity_is_not_hard_blocked(
    db_session: AsyncSession, make_tenant: TenantFactory
) -> None:
    tenant = await make_tenant()
    service = CatalogService(CatalogRepository(db_session))
    original = await service.create_item(tenant_id=tenant.id, fields=PRODUCT_IDENTITY)
    different_pack = await service.create_item(
        tenant_id=tenant.id, fields={**PRODUCT_IDENTITY, "pack_size": "56"}
    )
    incomplete = {key: value for key, value in PRODUCT_IDENTITY.items() if key != "dosage"}
    without_dosage = await service.create_item(tenant_id=tenant.id, fields=incomplete)
    assert len({original.id, different_pack.id, without_dosage.id}) == 3


async def test_identity_guard_is_tenant_scoped_but_barcode_stays_unique(
    db_session: AsyncSession, make_tenant: TenantFactory
) -> None:
    first, second = await make_tenant(), await make_tenant()
    service = CatalogService(CatalogRepository(db_session))
    barcode = BarcodeCreate(code="4607013192829")
    first_item = await service.create_item(
        tenant_id=first.id,
        fields=PRODUCT_IDENTITY,
        operation_id=uuid4(),
        initial_barcode=barcode,
    )
    second_item = await service.create_item(tenant_id=second.id, fields=PRODUCT_IDENTITY)
    assert first_item.id != second_item.id
    with pytest.raises(ConflictError) as same_identity_error:
        await service.create_item(
            tenant_id=first.id,
            fields=PRODUCT_IDENTITY,
            operation_id=uuid4(),
            initial_barcode=barcode,
        )
    assert same_identity_error.value.details == {"reason": "barcode_conflict"}
    with pytest.raises(ConflictError) as error:
        await service.create_item(
            tenant_id=first.id,
            fields={**PRODUCT_IDENTITY, "pack_size": "56"},
            operation_id=uuid4(),
            initial_barcode=barcode,
            confirm_distinct_item=True,
        )
    assert error.value.details == {"reason": "barcode_conflict"}


async def test_same_operation_and_barcode_are_explicitly_scoped_on_support_connection(
    db_session: AsyncSession, make_tenant: TenantFactory
) -> None:
    first, second = await make_tenant(), await make_tenant()
    service = CatalogService(CatalogRepository(db_session))
    operation_id = uuid4()
    barcode = BarcodeCreate(code="4607013192829")
    own = await service.create_item(
        tenant_id=first.id,
        fields={"brand_name": "Own"},
        operation_id=operation_id,
        initial_barcode=barcode,
    )
    other = await service.create_item(
        tenant_id=second.id,
        fields={"brand_name": "Other"},
        operation_id=operation_id,
        initial_barcode=barcode,
    )
    replay = await service.create_item(
        tenant_id=second.id,
        fields={"brand_name": "Other"},
        operation_id=operation_id,
        initial_barcode=barcode,
    )
    assert own.id != other.id and replay.id == other.id and replay.tenant_id == second.id


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE tenant_catalog SET operation_id=NULL,create_request_fingerprint=NULL WHERE id=:id",
        "UPDATE tenant_catalog SET create_request_fingerprint=repeat('a',64) WHERE id=:id",
        "UPDATE tenant_catalog SET operation_id=gen_random_uuid() WHERE id=:id",
        "UPDATE tenant_catalog SET id=gen_random_uuid() WHERE id=:id",
        "UPDATE tenant_catalog SET tenant_id=:other WHERE id=:id",
        "DELETE FROM tenant_catalog WHERE id=:id",
    ],
)
async def test_creation_identity_is_guarded_at_database_boundary(
    db_session: AsyncSession, make_tenant: TenantFactory, mutation: str
) -> None:
    tenant, other = await make_tenant(), await make_tenant()
    item = await CatalogService(CatalogRepository(db_session)).create_item(
        tenant_id=tenant.id, fields={"brand_name": "Protected"}, operation_id=uuid4()
    )
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(text(mutation), {"id": item.id, "other": other.id})


@pytest.mark.parametrize(
    "metadata",
    [
        "NULL, repeat('a',64)",
        "gen_random_uuid(),NULL",
        "gen_random_uuid(),'invalid'",
    ],
)
async def test_creation_metadata_pair_is_validated_in_sql(
    db_session: AsyncSession, make_tenant: TenantFactory, metadata: str
) -> None:
    tenant = await make_tenant()
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO tenant_catalog "
                    "(tenant_id,brand_name,operation_id,create_request_fingerprint) "
                    f"VALUES (:tenant,'Invalid',{metadata})"
                ),
                {"tenant": tenant.id},
            )


def test_initial_barcode_requires_uuid4_but_legacy_create_stays_compatible() -> None:
    assert CatalogItemCreate(brand_name="Legacy").operation_id is None
    with pytest.raises(SchemaValidationError):
        CatalogItemCreate(brand_name="Invalid", initial_barcode=BarcodeCreate(code="123"))
    with pytest.raises(SchemaValidationError):
        CatalogItemCreate(brand_name="Invalid", operation_id=uuid1())


async def test_http_create_permission_attaches_only_initial_barcode(
    client: AsyncClient, db_session: AsyncSession, make_tenant: TenantFactory
) -> None:
    tenant = await make_tenant()
    user = AppUser(
        email=f"catalog-create-{uuid4().hex}@example.invalid",
        full_name="Catalog creator",
        home_tenant_id=tenant.id,
        status="active",
    )
    db_session.add(user)
    await db_session.flush()
    actor = CurrentUser(
        user_id=user.id,
        tenant_id=tenant.id,
        is_developer=False,
        is_administrator=False,
        permissions={"catalog.create"},
    )

    async def override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def override_user() -> CurrentUser:
        return actor

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_user] = override_user
    try:
        payload = {
            "brand_name": "HTTP create",
            "operation_id": str(uuid4()),
            "initial_barcode": {"code": "4607013192829", "code_type": "ean13"},
        }
        response = await client.post("/api/v1/catalog", json=payload)
        assert response.status_code == 201, response.text
        item_id = response.json()["id"]
        assert "operation_id" not in response.json()
        assert "create_request_fingerprint" not in response.json()
        replay = await client.post("/api/v1/catalog", json=payload)
        assert replay.status_code == 201 and replay.json()["id"] == item_id
        denied = await client.post(
            f"/api/v1/catalog/{item_id}/barcodes", json={"code": "4607013192888"}
        )
        assert denied.status_code == 403
        actor.permissions = {"incoming.create"}
        denied_create = await client.post("/api/v1/catalog", json=payload)
        assert denied_create.status_code == 403
    finally:
        app.dependency_overrides.pop(current_user, None)
        app.dependency_overrides.pop(get_db, None)
