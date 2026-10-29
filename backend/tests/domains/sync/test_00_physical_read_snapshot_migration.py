"""Rehearse 0228 before physical writers; all DDL is rolled back in CI's test DB.

The 00 prefix is intentional. Pytest collects files in order and tests.sharding
filters whole files without reordering items. The first test therefore proves
the genuinely empty v2-history path rather than skipping it after installation.
"""

import importlib.util
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy import insert, text, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.domains.sync.models import (
    SyncReadProjection,
    SyncReadSnapshot,
    SyncReadSnapshotComponent,
)
from app.domains.sync.physical_read_snapshot import PhysicalRecord
from app.domains.sync.physical_read_snapshot_service import install_physical_read_snapshot
from app.domains.sync.read_snapshot import ReadSnapshotScope, build_manifest
from app.domains.sync.read_snapshot_projection import build_indexed_projection
from app.domains.sync.repository import SyncEdgeRepository
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.sync.test_physical_read_snapshot_integration import (
    SnapshotCase,
    prepare_snapshot_case,
    snapshot_rows,
)

db_session = receiving_session_fixture
SIGNATURES = (
    "public.trg_guard_sync_read_projection_header()",
    "public.trg_guard_sync_read_snapshot_storage()",
    "public.trg_guard_sync_read_snapshot_cursor()",
)
TABLES = (
    "sync_read_snapshot",
    "sync_read_snapshot_component",
    "sync_read_snapshot_chunk",
    "sync_read_projection",
)


def migration() -> ModuleType:
    path = Path(__file__).resolve().parents[3] / "alembic/versions/0228_physical_read_snapshot.py"
    spec = importlib.util.spec_from_file_location("physical_read_snapshot_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require_test_owner(connection: Connection) -> None:
    assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
    assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
    assert connection.scalar(text("SELECT current_user")) == "aurum_schema_owner"


def schema_facts(connection: Connection) -> dict[str, object]:
    functions = [
        connection.execute(
            text(
                "SELECT pg_get_functiondef(p.oid),pg_get_userbyid(p.proowner),"
                "p.prosecdef,p.proacl::text,p.proconfig FROM pg_proc p "
                "WHERE p.oid=to_regprocedure(:signature)"
            ),
            {"signature": signature},
        ).one()
        for signature in SIGNATURES
    ]
    constraints = list(
        connection.execute(
            text(
                "SELECT c.relname,k.conname,pg_get_constraintdef(k.oid) FROM pg_constraint k "
                "JOIN pg_class c ON c.oid=k.conrelid WHERE c.relname=ANY(:tables) "
                "ORDER BY c.relname,k.conname"
            ),
            {"tables": list(TABLES)},
        ).tuples()
    )
    columns = list(
        connection.execute(
            text(
                "SELECT a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,"
                "pg_get_expr(d.adbin,d.adrelid) FROM pg_attribute a LEFT JOIN pg_attrdef d "
                "ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
                "WHERE a.attrelid='public.sync_read_projection'::regclass "
                "AND a.attname IN ('payload','physical_count') "
                "AND NOT a.attisdropped ORDER BY a.attname"
            )
        ).tuples()
    )
    return {"functions": functions, "constraints": constraints, "columns": columns}


def data_fingerprints(connection: Connection, tenant_id: object) -> dict[str, tuple[object, ...]]:
    tables = (
        *TABLES,
        "sync_read_catalog_projection",
        "sync_read_inventory_projection",
        "sync_read_snapshot_cursor",
        "unit_stock_lot_v2",
        "unit_stock_opened_pack_v2",
        "unit_stock_movement_v2",
        "unit_stock_entry_v2",
        "audit_log",
    )
    return {
        table: tuple(
            connection.execute(
                text(
                    "SELECT count(*),md5(coalesce(string_agg(to_jsonb(r)::text,'' "
                    f"ORDER BY to_jsonb(r)::text),'')) FROM public.{table} r "
                    "WHERE tenant_id=:tenant"
                ),
                {"tenant": tenant_id},
            ).one()
        )
        for table in tables
    }


async def test_empty_0228_roundtrip_restores_guards_constraints_and_metadata(
    maintenance_engine: AsyncEngine,
) -> None:
    module = migration()

    def rehearse(connection: Connection) -> None:
        require_test_owner(connection)
        assert (
            connection.scalar(
                text(
                    "SELECT NOT EXISTS(SELECT 1 FROM sync_read_snapshot WHERE schema_version=2) "
                    "AND NOT EXISTS(SELECT 1 FROM sync_read_projection "
                    "WHERE projection_version=2 OR physical_count<>0)"
                )
            )
            is True
        ), "Empty migration rehearsal must precede physical snapshot writers"
        before = schema_facts(connection)
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
            assert schema_facts(connection)["columns"] == []
            for signature in SIGNATURES:
                definition = str(
                    connection.scalar(
                        text("SELECT pg_get_functiondef(to_regprocedure(:signature))"),
                        {"signature": signature},
                    )
                )
                assert module.PHYSICAL_GUARD not in definition
                assert module.STORAGE_GUARD not in definition
                assert module.CURSOR_GUARD not in definition
                if signature == module.HEADER_SIGNATURE:
                    assert definition.count(module.INVENTORY_FILTER) == 3
                    assert module.VERSIONED_FILTER not in definition
            module.upgrade()
        assert schema_facts(connection) == before

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


async def test_0228_downgrade_refuses_existing_v2_history_and_preserves_all_facts(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    case = await prepare_snapshot_case(db_session, maintenance_engine)
    await install_physical_read_snapshot(
        SyncEdgeRepository(db_session),
        envelope=case.envelope,
        chunks=case.chunks,
        credential=case.credential.token,
    )
    await db_session.commit()
    module = migration()

    def refuse(connection: Connection) -> None:
        require_test_owner(connection)
        before = schema_facts(connection)
        facts = data_fingerprints(connection, case.runtime.stock.tenant_id)
        point = connection.begin_nested()
        try:
            with Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(
                    DBAPIError, match="downgrade requires empty v2 history"
                ) as error:
                    module.downgrade()
                assert getattr(error.value.orig, "sqlstate", None) == "55000"
        finally:
            point.rollback()
        assert schema_facts(connection) == before
        assert data_fingerprints(connection, case.runtime.stock.tenant_id) == facts

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(refuse)
        finally:
            await transaction.rollback()


async def insert_draft(session: AsyncSession, case: SnapshotCase, *, version: int) -> None:
    manifest = case.envelope.manifest
    await session.execute(
        insert(SyncReadSnapshot).values(
            snapshot_id=manifest.snapshot_id,
            tenant_id=manifest.tenant_id,
            branch_id=manifest.branch_id,
            node_id=manifest.edge_node_id,
            schema_version=version,
            source_revision=manifest.source_revision,
            credential_kid=manifest.credential_kid,
            manifest_hash=case.envelope.manifest_hash,
            snapshot_hash=manifest.snapshot_hash,
            signature_algorithm=case.envelope.signature_algorithm,
            signature=case.envelope.signature,
            source_generated_at=manifest.source_generated_at,
            status="draft",
        )
    )


async def test_database_rejects_component_version_different_from_parent(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    case = await prepare_snapshot_case(db_session, maintenance_engine)
    await insert_draft(db_session, case, version=2)
    manifest = case.envelope.manifest
    component = manifest.components[0]
    with pytest.raises(DBAPIError, match="version differs from parent") as error:
        async with db_session.begin_nested():
            await db_session.execute(
                insert(SyncReadSnapshotComponent).values(
                    snapshot_id=manifest.snapshot_id,
                    tenant_id=manifest.tenant_id,
                    branch_id=manifest.branch_id,
                    node_id=manifest.edge_node_id,
                    source_revision=manifest.source_revision,
                    component=component.component,
                    schema_version=1,
                    item_count=component.item_count,
                    chunk_count=component.chunk_count,
                    component_hash=component.component_hash,
                )
            )
    assert getattr(error.value.orig, "sqlstate", None) == "23514"


async def test_v1_building_header_preserves_incomplete_legacy_construction_phase(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    case = await prepare_snapshot_case(db_session, maintenance_engine)
    await insert_draft(db_session, case, version=1)
    manifest = case.envelope.manifest
    await db_session.execute(
        insert(SyncReadProjection).values(
            snapshot_id=manifest.snapshot_id,
            tenant_id=manifest.tenant_id,
            branch_id=manifest.branch_id,
            node_id=manifest.edge_node_id,
            source_revision=manifest.source_revision,
            projection_version=1,
            status="building",
            foundation_payload={
                "record_type": "foundation",
                "tenant": {"id": str(manifest.tenant_id)},
                "branch": {"id": str(manifest.branch_id)},
            },
            catalog_count=0,
            supplier_count=0,
            inventory_count=1,
        )
    )
    assert (
        await db_session.scalar(
            text("SELECT status FROM sync_read_projection WHERE snapshot_id=:id"),
            {"id": manifest.snapshot_id},
        )
        == "building"
    )
    with pytest.raises(DBAPIError) as error:
        async with db_session.begin_nested():
            await db_session.execute(
                update(SyncReadProjection)
                .where(SyncReadProjection.snapshot_id == manifest.snapshot_id)
                .values(status="ready")
            )
    assert getattr(error.value.orig, "sqlstate", None) == "23514"


class UnvalidatedPhysicalRecord(BaseModel):
    """Bypass application DTO validation to exercise the independent SQL boundary."""

    model_config = ConfigDict(extra="allow")
    record_type: str | None


@pytest.mark.parametrize("forgery", ["unknown-kind", "null-kind", "duplicate", "header-mismatch"])
async def test_database_rejects_raw_physical_forgery_without_partial_storage(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, forgery: str
) -> None:
    case = await prepare_snapshot_case(db_session, maintenance_engine)
    projection = build_indexed_projection(
        tenant_id=case.runtime.stock.tenant_id,
        branch_id=case.runtime.stock.branch_id,
        chunks=case.chunks,
        schema_version=2,
    )
    physical = list(projection.physical)
    components = {
        component.component: tuple(
            tuple(deepcopy(item) for item in chunk.payload)
            for chunk in case.chunks
            if chunk.component == component.component
        )
        for component in case.envelope.manifest.components
    }
    if forgery == "header-mismatch":
        physical[-1] = physical[-1].model_copy(update={"is_blocked": True})
    elif forgery == "duplicate":
        physical.append(physical[-1])
    else:
        raw = physical[-1].model_dump(mode="json")
        raw["record_type"] = None if forgery == "null-kind" else "unsupported_physical"
        physical[-1] = cast(PhysicalRecord, UnvalidatedPhysicalRecord.model_validate(raw))
    if forgery != "header-mismatch":
        legacy = tuple(
            item
            for payload in components["inventory"]
            for item in payload
            if item.get("record_type") == "inventory_batch"
        )
        components["inventory"] = (legacy + tuple(row.model_dump(mode="json") for row in physical),)
    envelope, chunks = build_manifest(
        ReadSnapshotScope(
            edge_node_id=case.envelope.manifest.edge_node_id,
            tenant_id=case.runtime.stock.tenant_id,
            branch_id=case.runtime.stock.branch_id,
            source_revision=case.envelope.manifest.source_revision,
        ),
        components,
        credential_kid=case.credential.kid,
        credential_digest=case.credential.digest,
        source_generated_at=case.envelope.manifest.source_generated_at,
        schema_version=2,
    )
    before = await snapshot_rows(db_session, case.runtime.stock.tenant_id)
    with pytest.raises(DBAPIError) as error:
        async with db_session.begin_nested():
            await SyncEdgeRepository(db_session).install_read_snapshot(
                envelope=envelope,
                chunks=chunks,
                projection=replace(projection, physical=tuple(physical)),
            )
    assert getattr(error.value.orig, "sqlstate", None) == "23514"
    assert await snapshot_rows(db_session, case.runtime.stock.tenant_id) == before
