"""Exact SQL privileges and tenant isolation of the catalog worker login."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from decimal import Decimal
from unittest.mock import Mock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.domains.catalog.models import Barcode, CatalogImportJob, TenantCatalog
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.tasks import catalog as catalog_worker
from tests.domains.catalog.conftest import (
    committed_catalog_import as _committed_catalog_import_fixture,
)

committed_catalog_import = _committed_catalog_import_fixture

CATALOG_FIELDS = {
    "brand_name",
    "inn",
    "manufacturer",
    "form",
    "dosage",
    "pack_size",
    "atx_code",
    "dispensing_type",
    "storage_type",
    "category",
    "base_price",
}
WRITABLE_COLUMNS = {
    ("catalog_import_job", "UPDATE"): {
        "status",
        "execution_started_at",
        "total_rows",
        "valid_rows",
        "error_rows",
        "preview_data",
        "errors",
        "finished_at",
        "expires_at_for_rollback",
    },
    ("tenant_catalog", "INSERT"): CATALOG_FIELDS | {"tenant_id", "import_job_id", "created_by"},
    ("tenant_catalog", "UPDATE"): CATALOG_FIELDS,
    ("barcode", "INSERT"): {"tenant_id", "catalog_id", "code", "code_type"},
}


@pytest_asyncio.fixture
async def catalog_role_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(os.environ["DATABASE_URL_CATALOG_WORKER"], poolclass=NullPool)
    if not (engine.url.database or "").endswith("_test"):
        raise RuntimeError("Catalog worker privilege tests require a disposable '_test' database")
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_catalog_worker_identity_and_exact_acl(catalog_role_engine: AsyncEngine) -> None:
    async with catalog_role_engine.connect() as connection:
        identity = (await connection.execute(text("""
                SELECT session_user, current_user, role.rolconnlimit,
                  role.rolcanlogin, role.rolinherit, role.rolsuper, role.rolcreatedb,
                  role.rolcreaterole, role.rolreplication, role.rolbypassrls,
                  has_database_privilege(current_database(), 'CREATE') AS can_create,
                  has_database_privilege(current_database(), 'TEMP') AS can_temp,
                  has_schema_privilege('public', 'CREATE') AS can_create_object,
                  current_setting('idle_in_transaction_session_timeout') AS idle_limit,
                  current_setting('statement_timeout') AS statement_limit,
                  current_setting('lock_timeout') AS lock_limit,
                  NOT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_auth_members AS membership
                    WHERE role.oid IN (membership.member, membership.roleid)
                  ) AS no_membership,
                  NOT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_parameter_acl AS parameter
                    CROSS JOIN LATERAL pg_catalog.aclexplode(parameter.paracl) AS acl
                    WHERE acl.grantee = role.oid
                  ) AS no_parameter_privilege
                FROM pg_catalog.pg_roles AS role WHERE role.rolname = current_user
            """))).mappings().one()
        assert identity["session_user"] == identity["current_user"] == "aurum_catalog_worker"
        assert identity["rolcanlogin"] is True and identity["rolconnlimit"] == 2
        assert identity["no_membership"] is True
        assert identity["no_parameter_privilege"] is True
        for field in (
            "rolinherit",
            "rolsuper",
            "rolcreatedb",
            "rolcreaterole",
            "rolreplication",
            "rolbypassrls",
            "can_create",
            "can_temp",
            "can_create_object",
        ):
            assert identity[field] is False, field
        assert identity["idle_limit"] == "3min"
        assert identity["statement_limit"] == "30s"
        assert identity["lock_limit"] == "5s"

        tables = (await connection.execute(text("""
            SELECT relation.relname, privilege.name
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_namespace AS schema ON schema.oid = relation.relnamespace
            CROSS JOIN (VALUES ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE'),
              ('TRUNCATE'), ('REFERENCES'), ('TRIGGER')) AS privilege(name)
            WHERE schema.nspname = 'public' AND relation.relkind IN ('r','p','v','m')
              AND has_table_privilege(relation.oid, privilege.name)
        """))).all()
        assert set(tables) == {
            (table, "SELECT") for table in ("catalog_import_job", "tenant_catalog", "barcode")
        }
        columns = (await connection.execute(text("""
            SELECT relation.relname, privilege.name, column_info.attname
            FROM pg_catalog.pg_attribute AS column_info
            JOIN pg_catalog.pg_class AS relation ON relation.oid = column_info.attrelid
            JOIN pg_catalog.pg_namespace AS schema ON schema.oid = relation.relnamespace
            CROSS JOIN (VALUES ('INSERT'), ('UPDATE'), ('REFERENCES'),
              ('SELECT WITH GRANT OPTION'), ('INSERT WITH GRANT OPTION'),
              ('UPDATE WITH GRANT OPTION'), ('REFERENCES WITH GRANT OPTION')) AS privilege(name)
            WHERE schema.nspname = 'public' AND relation.relkind IN ('r','p','v','m')
              AND column_info.attnum > 0 AND NOT column_info.attisdropped
              AND has_column_privilege(relation.oid, column_info.attnum, privilege.name)
        """))).all()
        assert set(columns) == {
            (table, privilege, column)
            for (table, privilege), allowed in WRITABLE_COLUMNS.items()
            for column in allowed
        }
        assert not await connection.scalar(text("""
            SELECT EXISTS (
              SELECT 1 FROM pg_catalog.pg_attribute AS column_info
              JOIN pg_catalog.pg_class AS relation ON relation.oid = column_info.attrelid
              JOIN pg_catalog.pg_namespace AS schema ON schema.oid = relation.relnamespace
              WHERE schema.nspname = 'public' AND relation.relkind IN ('r','p','v','m')
                AND relation.relname NOT IN ('catalog_import_job','tenant_catalog','barcode')
                AND column_info.attnum > 0 AND NOT column_info.attisdropped
                AND has_column_privilege(relation.oid, column_info.attnum, 'SELECT')
            )
        """))

        functions = (await connection.execute(text("""
            SELECT routine.oid::REGPROCEDURE::TEXT
            FROM pg_catalog.pg_proc AS routine
            JOIN pg_catalog.pg_namespace AS schema ON schema.oid = routine.pronamespace
            WHERE schema.nspname = 'public' AND has_function_privilege(routine.oid, 'EXECUTE')
        """))).scalars().all()
        assert set(functions) == {"current_tenant_id()", "current_app_user_id()"}
        assert not await connection.scalar(text("""
            SELECT EXISTS (
              SELECT 1 FROM pg_catalog.pg_class AS relation
              JOIN pg_catalog.pg_namespace AS schema ON schema.oid = relation.relnamespace
              WHERE schema.nspname = 'public' AND relation.relkind = 'S'
                AND (has_sequence_privilege(relation.oid, 'USAGE')
                  OR has_sequence_privilege(relation.oid, 'SELECT')
                  OR has_sequence_privilege(relation.oid, 'UPDATE'))
            )
        """))


@pytest.mark.parametrize(
    ("tenant_value", "support_context", "expected"),
    [
        ("a6a71cd5-4078-48f1-8b28-5bb3f84779cd", "", "a6a71cd5-4078-48f1-8b28-5bb3f84779cd"),
        ("", "", None),
        ("malformed", "", None),
        ("a6a71cd5-4078-48f1-8b28-5bb3f84779cd", "forged", None),
    ],
)
async def test_catalog_tenant_helper_never_needs_support_privileges(
    catalog_role_engine: AsyncEngine,
    tenant_value: str,
    support_context: str,
    expected: str | None,
) -> None:
    async with catalog_role_engine.begin() as connection:
        await connection.execute(
            text(
                "SELECT set_config('app.tenant_id', :tenant, true), "
                "set_config('app.support_access_session_id', :support, true)"
            ),
            {"tenant": tenant_value, "support": support_context},
        )
        actual = await connection.scalar(text("SELECT public.current_tenant_id()::text"))
        assert actual == expected
        assert not await connection.scalar(
            text("SELECT has_function_privilege('public.is_tenant_support_session()', 'EXECUTE')")
        )


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM public.app_user LIMIT 0",
        "SELECT * FROM public.session LIMIT 0",
        "SELECT * FROM public.sale LIMIT 0",
        "SELECT * FROM public.audit_log LIMIT 0",
        "SELECT * FROM public.tenant_settings LIMIT 0",
        "SELECT * FROM public.batch LIMIT 0",
        "DELETE FROM public.tenant_catalog WHERE false",
        "UPDATE public.catalog_import_job SET source_path = 'forbidden' WHERE false",
        "UPDATE public.catalog_import_job SET execution_id = NULL WHERE false",
        "UPDATE public.catalog_import_job SET deadline_at = NULL WHERE false",
        "UPDATE public.catalog_import_job SET tenant_id = gen_random_uuid() WHERE false",
        "UPDATE public.tenant_catalog SET image_version = NULL WHERE false",
        "INSERT INTO public.tenant_catalog (tenant_id, brand_name, image_version) "
        "VALUES (gen_random_uuid(), 'Forbidden', NULL)",
        "CREATE TABLE public.catalog_worker_forbidden (id INTEGER)",
        "CREATE TEMP TABLE catalog_worker_forbidden (id INTEGER)",
        "SET ROLE aurum_app",
        "SET ROLE aurum_support",
        "SET ROLE aurum_schema_owner",
        "SELECT public.worker_purge_expired_sessions(1)",
    ],
)
async def test_catalog_worker_denies_other_capabilities(
    catalog_role_engine: AsyncEngine, statement: str
) -> None:
    async with catalog_role_engine.begin() as connection:
        with pytest.raises(DBAPIError) as denied:
            await connection.execute(text(statement))
        assert getattr(denied.value.orig, "sqlstate", None) == "42501"


@pytest.mark.parametrize("strategy", ["skip", "update", "create_copy"])
async def test_actual_worker_preview_and_all_duplicate_strategies(
    committed_catalog_import, strategy: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, tenant_id, job_id = committed_catalog_import
    assert catalog_worker.catalog_worker_engine.url.username == "aurum_catalog_worker"
    barcode = uuid4().hex
    raw = (
        "brand_name,base_price,barcode\n"
        "Existing role item,2.00,\n"
        f"New role item,3.00,{barcode}\n"
    ).encode()
    monkeypatch.setattr(catalog_worker, "get_catalog_object", Mock(return_value=raw))
    async with factory.begin() as session:
        service = CatalogService(CatalogRepository(session))
        existing = await service.create_item(
            tenant_id=tenant_id,
            fields={"brand_name": "Existing role item", "base_price": Decimal("1.00")},
        )
        existing_id = existing.id
        job = await service.request_preview(job_id=job_id)
        preview_execution = str(job.execution_id)
    preview = await catalog_worker._execute_job(
        str(job_id), str(tenant_id), preview_execution, phase="previewing"
    )
    assert preview == {"valid": 2, "errors": 0}
    async with factory.begin() as session:
        job = await CatalogService(CatalogRepository(session)).confirm_import(
            job_id=job_id, duplicate_strategy=strategy
        )
        import_execution = str(job.execution_id)
    imported = await catalog_worker._execute_job(
        str(job_id), str(tenant_id), import_execution, phase="importing"
    )
    assert imported == {"valid": 2, "errors": 0}
    async with factory.begin() as session:
        job = await session.get(CatalogImportJob, job_id)
        assert job is not None and job.status == "success"
        items = (
            await session.scalars(select(TenantCatalog).where(TenantCatalog.tenant_id == tenant_id))
        ).all()
        assert len(items) == (3 if strategy == "create_copy" else 2)
        original = next(item for item in items if item.id == existing_id)
        assert original.base_price == Decimal("2.00" if strategy == "update" else "1.00")
        codes = (
            await session.scalars(select(Barcode.code).where(Barcode.tenant_id == tenant_id))
        ).all()
        assert codes == [barcode]


async def test_catalog_worker_rls_and_narrow_insert_update(
    committed_catalog_import, catalog_role_engine: AsyncEngine
) -> None:
    _, tenant_id, job_id = committed_catalog_import
    factory = async_sessionmaker(catalog_role_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory.begin() as session:
        assert await session.scalar(text("SELECT count(*) FROM public.catalog_import_job")) == 0
        await session.execute(text("SELECT set_config('app.support_session', 'true', true)"))
        assert await session.scalar(text("SELECT count(*) FROM public.catalog_import_job")) == 0
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant, true)"), {"tenant": str(tenant_id)}
        )
        repo = CatalogRepository(session)
        job = await repo.get_job_for_update(job_id)
        assert job is not None
        item = await repo.create_import_item(
            tenant_id=tenant_id,
            import_job_id=job_id,
            created_by=job.user_id,
            brand_name="Catalog worker boundary",
            base_price=None,
        )
        await repo.update_item(item, brand_name="Updated catalog worker boundary")
        await CatalogService(repo).add_barcode(
            tenant_id=tenant_id, catalog_id=item.id, code=uuid4().hex, code_type="code128"
        )
        await repo.update_job(job, status="failed", errors=[])
        assert item.brand_name == "Updated catalog worker boundary"
        item_id = item.id
    async with factory.begin() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant, true)"), {"tenant": str(uuid4())}
        )
        assert await CatalogRepository(session).get_job(job_id) is None
        assert await CatalogRepository(session).get_item(item_id) is None
        assert await session.scalar(text("SELECT count(*) FROM public.barcode")) == 0
        with pytest.raises(DBAPIError) as denied:
            await session.execute(
                text(
                    "INSERT INTO public.tenant_catalog (tenant_id, brand_name) "
                    "VALUES (:tenant, 'Blocked')"
                ),
                {"tenant": tenant_id},
            )
        assert getattr(denied.value.orig, "sqlstate", None) == "42501"
