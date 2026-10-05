"""Preparation persists as metadata without changing stock or legacy creation intent."""

from __future__ import annotations

import importlib.util
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.core.config import get_settings
from app.core.deps import CurrentUser, current_user, get_db
from app.core.errors import ConflictError
from app.domains.auth.models import AppUser
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.schemas import CatalogItemCreate, CatalogItemRead, CatalogItemUpdate
from app.domains.catalog.service import CatalogService
from app.domains.foundation.models import Tenant
from app.main import app

type TenantFactory = Callable[[], Awaitable[Tenant]]


def _preparation(**overrides: object) -> dict[str, object]:
    return {
        "version": 1,
        "state": "prepared",
        "unit": "tablet",
        "units_per_pack": 30,
        "dispense_step": 1,
        "unit_price": "1.50",
        **overrides,
    }


@pytest.mark.parametrize("blister", [None, 10])
async def test_http_preparation_crud_keeps_catalog_permission_contract(
    client: AsyncClient, db_session: AsyncSession, make_tenant: TenantFactory, blister: int | None
) -> None:
    tenant = await make_tenant()
    user = AppUser(
        email=f"unit-preparation-{uuid4().hex}@example.invalid",
        full_name="Preparation editor",
        home_tenant_id=tenant.id,
        status="active",
    )
    db_session.add(user)
    await db_session.flush()
    actor = CurrentUser(
        user_id=user.id, tenant_id=tenant.id, is_developer=False, is_administrator=False
    )

    async def override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def override_user() -> CurrentUser:
        return actor

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_user] = override_user
    try:
        preparation = _preparation() if blister is None else _preparation(units_per_blister=blister)
        payload = {
            "operation_id": str(uuid4()),
            "brand_name": "HTTP preparation",
            "unit_sale_preparation": preparation,
        }
        denied = await client.post("/api/v1/catalog", json=payload)
        assert denied.status_code == 403
        actor.permissions = {"catalog.create"}
        actor.permission_scopes = {"catalog.create": None}
        created = await client.post("/api/v1/catalog", json=payload)
        assert created.status_code == 201, created.text
        assert created.json()["unit_sale_preparation"] == preparation
        item_id = created.json()["id"]
        denied_update = await client.patch(
            f"/api/v1/catalog/{item_id}", json={"unit_sale_preparation": None}
        )
        assert denied_update.status_code == 403
        actor.permissions = {"catalog.view"}
        actor.permission_scopes = {"catalog.view": None}
        read = await client.get(f"/api/v1/catalog/{item_id}")
        assert read.status_code == 200
        assert read.json()["unit_sale_preparation"] == preparation
        actor.permissions = {"catalog.update"}
        actor.permission_scopes = {"catalog.update": None}
        invalid = await client.patch(
            f"/api/v1/catalog/{item_id}",
            json={"unit_sale_preparation": _preparation(state="enabled")},
        )
        assert invalid.status_code == 422
        for invalid_blister in (True, 2.5, 7, 60, 10001):
            invalid_blister_response = await client.patch(
                f"/api/v1/catalog/{item_id}",
                json={"unit_sale_preparation": _preparation(units_per_blister=invalid_blister)},
            )
            assert invalid_blister_response.status_code == 422
        changed = await client.patch(
            f"/api/v1/catalog/{item_id}",
            json={"unit_sale_preparation": {**preparation, "unit_price": "2.50"}},
        )
        assert changed.status_code == 200
        assert changed.json()["unit_sale_preparation"] == {**preparation, "unit_price": "2.50"}
        if blister is not None:
            cleared_blister = await client.patch(
                f"/api/v1/catalog/{item_id}",
                json={"unit_sale_preparation": _preparation(units_per_blister=None)},
            )
            assert cleared_blister.status_code == 200
            assert cleared_blister.json()["unit_sale_preparation"] == _preparation()
            stored = await db_session.scalar(
                text("SELECT unit_sale_preparation FROM tenant_catalog WHERE id=:id"),
                {"id": UUID(item_id)},
            )
            assert stored == _preparation()
        cleared = await client.patch(
            f"/api/v1/catalog/{item_id}", json={"unit_sale_preparation": None}
        )
        assert cleared.status_code == 200 and cleared.json()["unit_sale_preparation"] is None
        assert (
            await db_session.scalar(
                text("SELECT count(*) FROM batch WHERE catalog_id=:id"), {"id": UUID(item_id)}
            )
            == 0
        )
    finally:
        app.dependency_overrides.pop(current_user, None)
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.parametrize("with_operation", [False, True])
async def test_preparation_create_read_update_clear(
    db_session: AsyncSession, make_tenant: TenantFactory, with_operation: bool
) -> None:
    tenant = await make_tenant()
    service = CatalogService(CatalogRepository(db_session))
    item = await service.create_item(
        tenant_id=tenant.id,
        fields={"brand_name": "Preparation", "unit_sale_preparation": _preparation()},
        operation_id=uuid4() if with_operation else None,
    )
    assert item.unit_sale_preparation == _preparation()
    public = CatalogItemRead.model_validate(item)
    assert public.unit_sale_preparation is not None
    assert public.unit_sale_preparation.state == "prepared"
    assert public.model_dump(mode="json")["unit_sale_preparation"] == _preparation()

    updated = await service.update_item(
        item.id,
        fields=CatalogItemUpdate(
            unit_sale_preparation=_preparation(unit="capsule", dispense_step=5, unit_price="2")
        ).model_dump(exclude_unset=True),
    )
    assert updated.unit_sale_preparation == _preparation(
        unit="capsule", dispense_step=5, unit_price="2.00"
    )
    unchanged = await service.update_item(item.id, fields={"category": "Prepared products"})
    assert unchanged.unit_sale_preparation == updated.unit_sale_preparation
    cleared = await service.update_item(
        item.id, fields=CatalogItemUpdate(unit_sale_preparation=None).model_dump(exclude_unset=True)
    )
    assert cleared.unit_sale_preparation is None
    assert (
        await db_session.scalar(
            text("SELECT unit_sale_preparation IS NULL FROM tenant_catalog WHERE id=:id"),
            {"id": item.id},
        )
        is True
    )


INVALID_OVERRIDES = [
    {"version": True},
    {"version": 1.0},
    {"version": 2},
    {"state": "enabled"},
    {"enabled": True},
    {"unit": "milliliter"},
    {"units_per_pack": 1},
    {"units_per_pack": 10001},
    {"units_per_pack": 30.0},
    {"units_per_pack": True},
    {"units_per_pack": "30"},
    {"units_per_blister": 1},
    {"units_per_blister": 10001},
    {"units_per_blister": 10.0},
    {"units_per_blister": 2.5},
    {"units_per_blister": True},
    {"units_per_blister": "10"},
    {"units_per_blister": 7},
    {"units_per_blister": 60},
    {"dispense_step": 0},
    {"dispense_step": 4},
    {"dispense_step": 31},
    {"dispense_step": 1.0},
    {"dispense_step": True},
    {"unit_price": "0"},
    {"unit_price": "-1"},
    {"unit_price": "1.001"},
    {"unit_price": "1000000000000.00"},
    {"unit_price": "NaN"},
    {"unit_price": "Infinity"},
]


@pytest.mark.parametrize("overrides", INVALID_OVERRIDES)
async def test_invalid_preparation_is_rejected_by_schema_and_direct_service(
    db_session: AsyncSession, make_tenant: TenantFactory, overrides: dict[str, object]
) -> None:
    tenant = await make_tenant()
    service = CatalogService(CatalogRepository(db_session))
    item = await service.create_item(tenant_id=tenant.id, fields={"brand_name": "Legacy"})
    preparation = _preparation(**overrides)
    with pytest.raises(ValidationError):
        CatalogItemCreate(brand_name="Invalid", unit_sale_preparation=preparation)
    with pytest.raises(ValidationError):
        CatalogItemUpdate(unit_sale_preparation=preparation)
    with pytest.raises(ValidationError):
        await service.create_item(
            tenant_id=tenant.id,
            fields={"brand_name": "Invalid", "unit_sale_preparation": preparation},
        )
    with pytest.raises(ValidationError):
        await service.update_item(item.id, fields={"unit_sale_preparation": preparation})
    assert (await service.get_item(item.id)).unit_sale_preparation is None


async def test_preparation_creation_retry_uses_canonical_price_and_original_intent(
    db_session: AsyncSession, make_tenant: TenantFactory
) -> None:
    tenant = await make_tenant()
    service = CatalogService(CatalogRepository(db_session))
    operation_id = uuid4()
    item = await service.create_item(
        tenant_id=tenant.id,
        fields={"brand_name": "Prepared", "unit_sale_preparation": _preparation(unit_price="2")},
        operation_id=operation_id,
    )
    replay = await service.create_item(
        tenant_id=tenant.id,
        fields={
            "brand_name": "Prepared",
            "unit_sale_preparation": _preparation(unit_price=Decimal("2.00")),
        },
        operation_id=operation_id,
    )
    assert replay.id == item.id
    await service.update_item(item.id, fields={"unit_sale_preparation": None})
    replay_after_clear = await service.create_item(
        tenant_id=tenant.id,
        fields={"brand_name": "Prepared", "unit_sale_preparation": _preparation(unit_price="2.0")},
        operation_id=operation_id,
    )
    assert replay_after_clear.id == item.id and replay_after_clear.unit_sale_preparation is None
    with pytest.raises(ConflictError):
        await service.create_item(
            tenant_id=tenant.id,
            fields={"brand_name": "Prepared", "unit_sale_preparation": _preparation()},
            operation_id=operation_id,
        )


async def test_null_preparation_preserves_pre_migration_creation_fingerprint(
    db_session: AsyncSession, make_tenant: TenantFactory
) -> None:
    tenant = await make_tenant()
    service = CatalogService(CatalogRepository(db_session))
    operation_id = uuid4()
    request = CatalogItemCreate(brand_name="Legacy", base_price=Decimal("1"))
    canonical = request.model_dump(
        mode="json", exclude={"operation_id", "confirm_distinct_item", "unit_sale_preparation"}
    )
    canonical["base_price"] = "1.00"
    legacy_fingerprint = sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    # Simulate a row created by the preceding release, with its immutable original hash.
    legacy = await CatalogRepository(db_session).create_item(
        tenant_id=tenant.id,
        brand_name="Legacy",
        base_price=Decimal("1"),
        operation_id=operation_id,
        create_request_fingerprint=legacy_fingerprint,
    )
    for fields in (
        {"brand_name": "Legacy", "base_price": Decimal("1.00")},
        {"brand_name": "Legacy", "base_price": Decimal("1.00"), "unit_sale_preparation": None},
    ):
        replay = await service.create_item(
            tenant_id=tenant.id, fields=fields, operation_id=operation_id
        )
        assert replay.id == legacy.id


async def test_optional_blister_preserves_legacy_prepared_creation_fingerprint(
    db_session: AsyncSession, make_tenant: TenantFactory
) -> None:
    tenant = await make_tenant()
    operation_id = uuid4()
    fields = {"brand_name": "Legacy prepared", "unit_sale_preparation": _preparation()}
    canonical = CatalogItemCreate.model_validate(fields).model_dump(
        mode="json", exclude={"operation_id", "confirm_distinct_item"}
    )
    # Reconstruct the previous release contract independently of the new serializer.
    canonical["unit_sale_preparation"] = _preparation()
    fingerprint = sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    legacy = await CatalogRepository(db_session).create_item(
        tenant_id=tenant.id,
        brand_name="Legacy prepared",
        unit_sale_preparation=_preparation(),
        operation_id=operation_id,
        create_request_fingerprint=fingerprint,
    )
    service = CatalogService(CatalogRepository(db_session))
    for preparation in (_preparation(), _preparation(units_per_blister=None)):
        replay = await service.create_item(
            tenant_id=tenant.id,
            fields={**fields, "unit_sale_preparation": preparation},
            operation_id=operation_id,
        )
        assert replay.id == legacy.id
    with pytest.raises(ConflictError):
        await service.create_item(
            tenant_id=tenant.id,
            fields={**fields, "unit_sale_preparation": _preparation(units_per_blister=10)},
            operation_id=operation_id,
        )


async def test_blister_creation_retry_preserves_original_configuration(
    db_session: AsyncSession, make_tenant: TenantFactory
) -> None:
    tenant = await make_tenant()
    operation_id = uuid4()
    service = CatalogService(CatalogRepository(db_session))
    fields = {
        "brand_name": "Blister prepared",
        "unit_sale_preparation": _preparation(units_per_blister=10, unit_price="2"),
    }
    item = await service.create_item(tenant_id=tenant.id, fields=fields, operation_id=operation_id)
    replay = await service.create_item(
        tenant_id=tenant.id,
        fields={
            **fields,
            "unit_sale_preparation": _preparation(units_per_blister=10, unit_price="2.00"),
        },
        operation_id=operation_id,
    )
    assert replay.id == item.id
    for blister in (None, 5, 30):
        with pytest.raises(ConflictError):
            await service.create_item(
                tenant_id=tenant.id,
                fields={
                    **fields,
                    "unit_sale_preparation": _preparation(
                        units_per_blister=blister, unit_price="2"
                    ),
                },
                operation_id=operation_id,
            )


@pytest.mark.parametrize(
    "metadata",
    [
        None,  # JSON null is not the SQL NULL used to clear preparation.
        [],
        {},
        {"version": 1},
        _preparation(version=True),
        _preparation(version=1.0),
        _preparation(state="enabled"),
        _preparation(enabled=True),
        _preparation(units_per_pack="30"),
        _preparation(units_per_pack=30.0),
        _preparation(units_per_pack=10000000000000000000000),
        _preparation(units_per_blister=None),
        _preparation(units_per_blister=True),
        _preparation(units_per_blister="10"),
        _preparation(units_per_blister=10.0),
        _preparation(units_per_blister=2.5),
        _preparation(units_per_blister=0),
        _preparation(units_per_blister=1),
        _preparation(units_per_blister=10001),
        _preparation(units_per_blister=10000000000000000000000),
        _preparation(units_per_blister=7),
        _preparation(units_per_blister=60),
        _preparation(dispense_step=0),
        _preparation(dispense_step=4),
        _preparation(unit_price=1.5),
        _preparation(unit_price="1.001"),
        _preparation(unit_price="NaN"),
        _preparation(unit_price="0.00"),
        _preparation(unit_price="1000000000000.00"),
        _preparation(unit=None),
    ],
)
async def test_database_rejects_invalid_preparation_without_nullable_check_bypass(
    db_session: AsyncSession, make_tenant: TenantFactory, metadata: object
) -> None:
    tenant = await make_tenant()
    item = await CatalogService(CatalogRepository(db_session)).create_item(
        tenant_id=tenant.id, fields={"brand_name": "Database guard"}
    )
    with pytest.raises(IntegrityError, match="ck_tc_unit_sale_preparation"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "UPDATE tenant_catalog SET unit_sale_preparation=CAST(:value AS jsonb) "
                    "WHERE id=:id"
                ),
                {"value": json.dumps(metadata), "id": item.id},
            )


@pytest.mark.parametrize(("pack", "blister"), [(2, 2), (30, 10), (30, 30), (10000, 10000)])
async def test_database_accepts_blister_boundaries(
    db_session: AsyncSession, make_tenant: TenantFactory, pack: int, blister: int
) -> None:
    tenant = await make_tenant()
    item = await CatalogService(CatalogRepository(db_session)).create_item(
        tenant_id=tenant.id, fields={"brand_name": "Database blister boundary"}
    )
    metadata = _preparation(units_per_pack=pack, units_per_blister=blister)
    await db_session.execute(
        text(
            "UPDATE tenant_catalog SET unit_sale_preparation=CAST(:value AS jsonb) " "WHERE id=:id"
        ),
        {"value": json.dumps(metadata), "id": item.id},
    )
    assert (
        await db_session.scalar(
            text("SELECT unit_sale_preparation FROM tenant_catalog WHERE id=:id"), {"id": item.id}
        )
        == metadata
    )


@pytest.mark.parametrize("blister", [None, 10])
async def test_preparation_obeys_existing_tenant_rls(
    db_engine: AsyncEngine, maintenance_engine: AsyncEngine, blister: int | None
) -> None:
    preparation = _preparation() if blister is None else _preparation(units_per_blister=blister)
    app_engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    for engine in (db_engine, maintenance_engine, app_engine):
        assert (engine.url.database or "").endswith("_test")
    tenant_ids: list[UUID] = []
    try:
        async with db_engine.begin() as connection:
            await connection.execute(text("SELECT set_config('app.support_session','true',true)"))
            for _index in range(2):
                tenant_id = await connection.scalar(
                    text(
                        "INSERT INTO tenant(name,contact_email) VALUES ('Preparation isolation',"
                        " :email) RETURNING id"
                    ),
                    {"email": f"unit-{uuid4().hex}@example.invalid"},
                )
                assert isinstance(tenant_id, UUID)
                tenant_ids.append(tenant_id)
            catalog_ids = []
            for tenant_id in tenant_ids:
                catalog_ids.append(
                    await connection.scalar(
                        text(
                            "INSERT INTO tenant_catalog"
                            "(tenant_id,brand_name,unit_sale_preparation) "
                            "VALUES (:tenant,'Prepared',CAST(:metadata AS jsonb)) RETURNING id"
                        ),
                        {"tenant": tenant_id, "metadata": json.dumps(preparation)},
                    )
                )
        async with app_engine.begin() as connection:
            assert await connection.scalar(text("SELECT session_user")) == "aurum_app"
            await connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant, true)"),
                {"tenant": str(tenant_ids[0])},
            )
            rows = (
                await connection.execute(
                    text(
                        "SELECT id,unit_sale_preparation FROM tenant_catalog "
                        "WHERE tenant_id=ANY(:tenants)"
                    ),
                    {"tenants": tenant_ids},
                )
            ).all()
            assert rows == [(catalog_ids[0], preparation)]
            hidden = await connection.execute(
                text("UPDATE tenant_catalog SET unit_sale_preparation=NULL WHERE id=:id"),
                {"id": catalog_ids[1]},
            )
            assert hidden.rowcount == 0
    finally:
        await app_engine.dispose()
        if tenant_ids:
            async with maintenance_engine.begin() as connection:
                await connection.execute(
                    text("DELETE FROM tenant_catalog WHERE tenant_id=ANY(:tenants)"),
                    {"tenants": tenant_ids},
                )
                await connection.execute(
                    text("DELETE FROM audit_log WHERE tenant_id=ANY(:tenants)"),
                    {"tenants": tenant_ids},
                )
            async with db_engine.begin() as connection:
                await connection.execute(
                    text("SELECT set_config('app.support_session','true',true)")
                )
                await connection.execute(
                    text("DELETE FROM tenant WHERE id=ANY(:tenants)"), {"tenants": tenant_ids}
                )


async def test_preparation_migration_roundtrip_preserves_legacy_catalog(
    maintenance_engine: AsyncEngine,
) -> None:
    source = (
        Path(__file__).resolve().parents[3]
        / "alembic"
        / "versions"
        / ("0161_catalog_unit_sale_preparation.py")
    )
    spec = importlib.util.spec_from_file_location("unit_sale_preparation_migration", source)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        security_sql = text(
            "SELECT relrowsecurity,relforcerowsecurity,relowner,relacl::text FROM pg_class "
            "WHERE oid='public.tenant_catalog'::regclass"
        )
        policies_sql = text(
            "SELECT polname,polcmd,polpermissive,polroles,pg_get_expr(polqual,polrelid), "
            "pg_get_expr(polwithcheck,polrelid) FROM pg_policy "
            "WHERE polrelid='public.tenant_catalog'::regclass ORDER BY polname"
        )
        contents_sql = text(
            "SELECT id,to_jsonb(catalog)-'unit_sale_preparation' FROM tenant_catalog catalog "
            "ORDER BY id"
        )
        column_sql = text(
            "SELECT data_type,is_nullable FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='tenant_catalog' "
            "AND column_name='unit_sale_preparation'"
        )
        before_security = connection.execute(security_sql).one()
        before_policies = connection.execute(policies_sql).all()
        before_contents = connection.execute(contents_sql).all()
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            assert connection.execute(column_sql).first() is None
            assert connection.execute(contents_sql).all() == before_contents
            tenant_id = connection.scalar(
                text(
                    "INSERT INTO tenant(name,contact_email) VALUES ('Legacy migration', :email) "
                    "RETURNING id"
                ),
                {"email": f"migration-unit-{uuid4().hex}@example.invalid"},
            )
            connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant, true)"),
                {"tenant": str(tenant_id)},
            )
            legacy_id = connection.scalar(
                text(
                    "INSERT INTO tenant_catalog(tenant_id,brand_name,pack_size,base_price) "
                    "VALUES (:tenant,'Legacy after downgrade','30 pieces',15.50) RETURNING id"
                ),
                {"tenant": tenant_id},
            )
            legacy_snapshot = connection.scalar(
                text("SELECT to_jsonb(catalog) FROM tenant_catalog catalog WHERE id=:id"),
                {"id": legacy_id},
            )
            migration.upgrade()
        assert connection.execute(column_sql).one() == ("jsonb", "YES")
        assert connection.execute(security_sql).one() == before_security
        assert connection.execute(policies_sql).all() == before_policies
        assert (
            connection.scalar(
                text(
                    "SELECT to_jsonb(catalog)-'unit_sale_preparation' FROM tenant_catalog catalog "
                    "WHERE id=:id"
                ),
                {"id": legacy_id},
            )
            == legacy_snapshot
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM tenant_catalog WHERE unit_sale_preparation IS NOT NULL")
            )
            == 0
        )

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


async def test_blister_migration_roundtrip_refuses_data_loss(
    maintenance_engine: AsyncEngine,
) -> None:
    source = (
        Path(__file__).resolve().parents[3]
        / "alembic"
        / "versions"
        / "0173_catalog_blister_preparation.py"
    )
    spec = importlib.util.spec_from_file_location("catalog_blister_migration", source)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        tenant_id = connection.scalar(
            text(
                "INSERT INTO tenant(name,contact_email) VALUES ('Blister migration', :email) "
                "RETURNING id"
            ),
            {"email": f"migration-blister-{uuid4().hex}@example.invalid"},
        )
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant, true)"),
            {"tenant": str(tenant_id)},
        )
        metadata = _preparation(units_per_blister=10)
        item_id = connection.scalar(
            text(
                "INSERT INTO tenant_catalog(tenant_id,brand_name,unit_sale_preparation) "
                "VALUES (:tenant,'Blister migration',CAST(:metadata AS jsonb)) RETURNING id"
            ),
            {"tenant": tenant_id, "metadata": json.dumps(metadata)},
        )
        select_metadata = text("SELECT unit_sale_preparation FROM tenant_catalog WHERE id=:id")
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(DBAPIError, match="Clear catalog units_per_blister"):
                with connection.begin_nested():
                    migration.downgrade()
            assert connection.scalar(select_metadata, {"id": item_id}) == metadata
            connection.execute(
                text(
                    "UPDATE tenant_catalog SET unit_sale_preparation="
                    "unit_sale_preparation-'units_per_blister' WHERE id=:id"
                ),
                {"id": item_id},
            )
            migration.downgrade()
            assert connection.scalar(select_metadata, {"id": item_id}) == _preparation()
            with pytest.raises(IntegrityError, match="ck_tc_unit_sale_preparation"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE tenant_catalog SET unit_sale_preparation="
                            "CAST(:metadata AS jsonb) WHERE id=:id"
                        ),
                        {"metadata": json.dumps(metadata), "id": item_id},
                    )
            migration.upgrade()
            connection.execute(
                text(
                    "UPDATE tenant_catalog SET unit_sale_preparation="
                    "CAST(:metadata AS jsonb) WHERE id=:id"
                ),
                {"metadata": json.dumps(metadata), "id": item_id},
            )
            assert connection.scalar(select_metadata, {"id": item_id}) == metadata

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
