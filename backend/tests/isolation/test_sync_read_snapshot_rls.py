"""Database boundaries for independent read-only Edge snapshots."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domains.sync.models import (
    SyncReadBarcodeProjection,
    SyncReadCatalogProjection,
    SyncReadInventoryProjection,
    SyncReadInventoryRollup,
    SyncReadProjection,
    SyncReadSnapshot,
    SyncReadSnapshotChunk,
    SyncReadSnapshotComponent,
    SyncReadSnapshotCursor,
    SyncReadSupplierProjection,
)

SNAPSHOT_TABLES = {
    "sync_read_snapshot",
    "sync_read_snapshot_component",
    "sync_read_snapshot_chunk",
    "sync_read_snapshot_cursor",
    "sync_read_projection",
    "sync_read_catalog_projection",
    "sync_read_barcode_projection",
    "sync_read_supplier_projection",
    "sync_read_inventory_projection",
    "sync_read_inventory_rollup",
}


@pytest_asyncio.fixture
async def support_engine_read_snapshot() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_SUPPORT, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def app_engine_read_snapshot() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _set_edge_scope(
    connection: AsyncConnection,
    *,
    tenant_id: UUID,
    branch_id: UUID,
    node_id: UUID,
) -> None:
    for setting, value in (
        ("app.tenant_id", tenant_id),
        ("app.branch_id", branch_id),
        ("app.edge_node_id", node_id),
    ):
        await connection.execute(
            text("SELECT set_config(:setting, :value, true)"),
            {"setting": setting, "value": str(value)},
        )


def test_read_snapshot_model_metadata_matches_storage_contract() -> None:
    assert {
        SyncReadSnapshot.__tablename__,
        SyncReadSnapshotComponent.__tablename__,
        SyncReadSnapshotChunk.__tablename__,
        SyncReadSnapshotCursor.__tablename__,
        SyncReadProjection.__tablename__,
        SyncReadCatalogProjection.__tablename__,
        SyncReadBarcodeProjection.__tablename__,
        SyncReadSupplierProjection.__tablename__,
        SyncReadInventoryProjection.__tablename__,
        SyncReadInventoryRollup.__tablename__,
    } == SNAPSHOT_TABLES
    assert {
        "source_revision",
        "credential_kid",
        "manifest_hash",
        "snapshot_hash",
        "signature_algorithm",
        "signature",
    } <= set(SyncReadSnapshot.__table__.columns.keys())
    assert {"payload", "payload_hash", "source_revision"} <= set(
        SyncReadSnapshotChunk.__table__.columns.keys()
    )
    assert SyncReadSnapshotCursor.__table__.primary_key.columns.keys() == [
        "tenant_id",
        "branch_id",
        "node_id",
    ]


async def test_read_snapshot_tables_force_rls_and_limit_runtime_writes(
    support_engine_read_snapshot: AsyncEngine,
) -> None:
    async with support_engine_read_snapshot.connect() as connection:
        relations = (
            await connection.execute(
                text("""
                    SELECT relname, relrowsecurity, relforcerowsecurity
                    FROM pg_catalog.pg_class
                    WHERE relnamespace = 'public'::regnamespace
                      AND relname = ANY(:tables)
                    """),
                {"tables": sorted(SNAPSHOT_TABLES)},
            )
        ).mappings()
        policies = (
            await connection.execute(
                text("""
                    SELECT tablename, policyname, qual, with_check
                    FROM pg_catalog.pg_policies
                    WHERE schemaname = 'public'
                      AND tablename = ANY(:tables)
                    """),
                {"tables": sorted(SNAPSHOT_TABLES)},
            )
        ).mappings()
        privileges = (
            await connection.execute(
                text("""
                    SELECT
                      relname,
                      has_table_privilege('aurum_app', oid, 'SELECT') AS can_select,
                      has_table_privilege('aurum_app', oid, 'INSERT') AS can_insert,
                      has_table_privilege('aurum_app', oid, 'UPDATE') AS can_update,
                      has_table_privilege('aurum_app', oid, 'DELETE') AS can_delete
                    FROM pg_catalog.pg_class
                    WHERE relnamespace = 'public'::regnamespace
                      AND relname = ANY(:tables)
                    """),
                {"tables": sorted(SNAPSHOT_TABLES)},
            )
        ).mappings()
        constraints = set(
            (
                await connection.execute(
                    text("""
                        SELECT conname
                        FROM pg_catalog.pg_constraint
                        WHERE connamespace = 'public'::regnamespace
                          AND conname = ANY(:constraints)
                        """),
                    {
                        "constraints": [
                            "ck_sync_read_projection_foundation_scope",
                            "ck_sync_read_catalog_projection_payload",
                            "ck_sync_read_supplier_projection_payload",
                            "ck_sync_read_inventory_projection_payload",
                            "ck_sync_read_inventory_rollup_batch_count",
                            "ck_sync_read_inventory_rollup_blocked_count",
                            "ck_sync_read_inventory_rollup_blocked_within_total",
                        ]
                    },
                )
            ).scalars()
        )

    assert {row["relname"] for row in relations} == SNAPSHOT_TABLES
    assert all(row["relrowsecurity"] and row["relforcerowsecurity"] for row in relations)
    assert {(row["tablename"], row["policyname"]) for row in policies} == {
        (table, "tenant_isolation") for table in SNAPSHOT_TABLES
    }
    for policy in policies:
        assert "current_tenant_id" in policy["qual"]
        assert "app.branch_id" in policy["qual"]
        assert "app.edge_node_id" in policy["qual"]
        assert policy["with_check"] is not None
    privilege_by_table = {row["relname"]: row for row in privileges}
    assert all(row["can_select"] for row in privileges)
    assert privilege_by_table["sync_read_snapshot"]["can_insert"]
    assert privilege_by_table["sync_read_snapshot"]["can_update"]
    assert privilege_by_table["sync_read_snapshot_component"]["can_insert"]
    assert not privilege_by_table["sync_read_snapshot_component"]["can_update"]
    assert privilege_by_table["sync_read_snapshot_chunk"]["can_insert"]
    assert not privilege_by_table["sync_read_snapshot_chunk"]["can_update"]
    assert privilege_by_table["sync_read_snapshot_cursor"]["can_insert"]
    assert privilege_by_table["sync_read_snapshot_cursor"]["can_update"]
    assert privilege_by_table["sync_read_projection"]["can_insert"]
    assert privilege_by_table["sync_read_projection"]["can_update"]
    assert privilege_by_table["sync_read_catalog_projection"]["can_insert"]
    assert not privilege_by_table["sync_read_catalog_projection"]["can_update"]
    assert privilege_by_table["sync_read_inventory_rollup"]["can_insert"]
    assert not privilege_by_table["sync_read_inventory_rollup"]["can_update"]
    assert all(not row["can_delete"] for row in privileges)
    assert constraints == {
        "ck_sync_read_projection_foundation_scope",
        "ck_sync_read_catalog_projection_payload",
        "ck_sync_read_supplier_projection_payload",
        "ck_sync_read_inventory_projection_payload",
        "ck_sync_read_inventory_rollup_batch_count",
        "ck_sync_read_inventory_rollup_blocked_count",
        "ck_sync_read_inventory_rollup_blocked_within_total",
    }


async def test_catalog_search_function_has_a_narrow_security_boundary(
    support_engine_read_snapshot: AsyncEngine,
) -> None:
    async with support_engine_read_snapshot.connect() as connection:
        function = (await connection.execute(text("""
                    SELECT
                      pg_catalog.pg_get_userbyid(routine.proowner) AS owner,
                      language.lanname AS language,
                      routine.prosecdef,
                      routine.provolatile::TEXT AS provolatile,
                      routine.proconfig,
                      pg_catalog.has_function_privilege(
                        'aurum_app', routine.oid, 'EXECUTE'
                      ) AS app_can_execute,
                      pg_catalog.has_function_privilege(
                        'aurum_app', routine.oid, 'EXECUTE WITH GRANT OPTION'
                      ) AS app_can_grant,
                      pg_catalog.has_function_privilege(
                        'aurum_support', routine.oid, 'EXECUTE'
                      ) AS support_can_execute,
                      EXISTS (
                        SELECT 1
                        FROM pg_catalog.aclexplode(
                          COALESCE(
                            routine.proacl,
                            pg_catalog.acldefault('f'::"char", routine.proowner)
                          )
                        ) AS acl
                        WHERE acl.grantee = 0
                          AND acl.privilege_type = 'EXECUTE'
                      ) AS public_can_execute
                    FROM pg_catalog.pg_proc AS routine
                    JOIN pg_catalog.pg_language AS language
                      ON language.oid = routine.prolang
                    WHERE routine.oid = pg_catalog.to_regprocedure(
                      'public.search_sync_read_catalog(uuid,text)'
                    )
                    """))).mappings().one()

    assert dict(function) == {
        "owner": "aurum_schema_owner",
        "language": "plpgsql",
        "prosecdef": True,
        "provolatile": "s",
        "proconfig": ["search_path=pg_catalog, pg_temp", "row_security=off"],
        "app_can_execute": True,
        "app_can_grant": False,
        "support_can_execute": False,
        "public_can_execute": False,
    }


async def test_edge_runtime_installs_own_snapshot_and_cannot_write_another_node(
    support_engine_read_snapshot: AsyncEngine,
    app_engine_read_snapshot: AsyncEngine,
) -> None:
    async with support_engine_read_snapshot.begin() as connection:
        tenant_id = (
            await connection.execute(
                text(
                    "INSERT INTO tenant (name, contact_email) "
                    "VALUES (:name, :email) RETURNING id"
                ),
                {
                    "name": f"Read snapshot {uuid4().hex[:8]}",
                    "email": f"read-snapshot-{uuid4().hex[:8]}@aurum.tj",
                },
            )
        ).scalar_one()
        branch_id = (
            await connection.execute(
                text(
                    "INSERT INTO branch (tenant_id, name) "
                    "VALUES (:tenant_id, 'Snapshot branch') RETURNING id"
                ),
                {"tenant_id": tenant_id},
            )
        ).scalar_one()
        cloud_node_id = (
            await connection.execute(
                text(
                    "SELECT writer_node_id FROM sync_stream "
                    "WHERE tenant_id = :tenant_id AND branch_id = :branch_id"
                ),
                {"tenant_id": tenant_id, "branch_id": branch_id},
            )
        ).scalar_one()
        node_rows = await connection.execute(
            text("""
                INSERT INTO sync_node (
                  tenant_id, branch_id, node_kind, mode, status, display_name,
                  credential_kid, credential_hash, credential_issued_at,
                  credential_expires_at, shadow_start_origin_node_id,
                  shadow_start_writer_epoch
                ) VALUES
                  (:tenant_id, :branch_id, 'edge', 'shadow_readonly', 'active',
                   'Snapshot A', gen_random_uuid(), :credential_hash_a, now(),
                   now() + interval '1 day', :cloud_node_id, 1),
                  (:tenant_id, :branch_id, 'edge', 'shadow_readonly', 'active',
                   'Snapshot B', gen_random_uuid(), :credential_hash_b, now(),
                   now() + interval '1 day', :cloud_node_id, 1)
                RETURNING id
                """),
            {
                "tenant_id": tenant_id,
                "branch_id": branch_id,
                "cloud_node_id": cloud_node_id,
                "credential_hash_a": uuid4().hex * 2,
                "credential_hash_b": uuid4().hex * 2,
            },
        )
        node_ids = [row[0] for row in node_rows]

    snapshot_id = uuid4()
    async with app_engine_read_snapshot.connect() as connection:
        transaction = await connection.begin()
        try:
            await _set_edge_scope(
                connection,
                tenant_id=tenant_id,
                branch_id=branch_id,
                node_id=node_ids[0],
            )
            await connection.execute(
                text("""
                    INSERT INTO sync_read_snapshot (
                      snapshot_id, tenant_id, branch_id, node_id, schema_version,
                      source_revision, credential_kid, manifest_hash, snapshot_hash,
                      signature_algorithm, signature, source_generated_at
                    ) VALUES (
                      :snapshot_id, :tenant_id, :branch_id, :node_id, 1, 1,
                      gen_random_uuid(), repeat('1', 64), repeat('2', 64),
                      'hmac-sha256-edge-read-snapshot-v1', repeat('3', 64), now()
                    )
                    """),
                {
                    "snapshot_id": snapshot_id,
                    "tenant_id": tenant_id,
                    "branch_id": branch_id,
                    "node_id": node_ids[0],
                },
            )
            for index, component in enumerate(("foundation", "catalog", "inventory"), 4):
                params = {
                    "snapshot_id": snapshot_id,
                    "tenant_id": tenant_id,
                    "branch_id": branch_id,
                    "node_id": node_ids[0],
                    "component": component,
                    "hash": str(index) * 64,
                    "item_count": 1 if component == "foundation" else 0,
                }
                await connection.execute(
                    text("""
                        INSERT INTO sync_read_snapshot_component (
                          snapshot_id, component, tenant_id, branch_id, node_id,
                          source_revision, schema_version, item_count, chunk_count,
                          component_hash
                        ) VALUES (
                          :snapshot_id, :component, :tenant_id, :branch_id, :node_id,
                          1, 1, :item_count, 1, :hash
                        )
                        """),
                    params,
                )
                await connection.execute(
                    text("""
                        INSERT INTO sync_read_snapshot_chunk (
                          snapshot_id, component, chunk_index, tenant_id, branch_id,
                          node_id, source_revision, schema_version, item_count,
                          payload, payload_hash
                        ) VALUES (
                          :snapshot_id, :component, 0, :tenant_id, :branch_id,
                          :node_id, 1, 1, :item_count,
                          CASE WHEN :component = 'foundation' THEN
                            jsonb_build_object('items', jsonb_build_array(
                              jsonb_build_object(
                                'record_type', 'foundation',
                                'tenant', jsonb_build_object(
                                  'id', to_jsonb(CAST(:tenant_id AS UUID))
                                ),
                                'branch', jsonb_build_object(
                                  'id', to_jsonb(CAST(:branch_id AS UUID))
                                )
                              )
                            ))
                          ELSE '{"items": []}'::jsonb END,
                          :hash
                        )
                        """),
                    params,
                )
            await connection.execute(
                text("""
                    INSERT INTO sync_read_projection (
                      snapshot_id, tenant_id, branch_id, node_id, source_revision,
                      foundation_payload, catalog_count, supplier_count, inventory_count
                    ) VALUES (
                      :snapshot_id, :tenant_id, :branch_id, :node_id, 1,
                      jsonb_build_object(
                        'record_type', 'foundation',
                        'tenant', jsonb_build_object(
                          'id', to_jsonb(CAST(:tenant_id AS UUID))
                        ),
                        'branch', jsonb_build_object(
                          'id', to_jsonb(CAST(:branch_id AS UUID))
                        )
                      ), 0, 0, 0
                    )
                    """),
                {
                    "snapshot_id": snapshot_id,
                    "tenant_id": tenant_id,
                    "branch_id": branch_id,
                    "node_id": node_ids[0],
                },
            )
            await connection.execute(
                text(
                    "UPDATE sync_read_projection SET status = 'ready' "
                    "WHERE snapshot_id = :snapshot_id"
                ),
                {"snapshot_id": snapshot_id},
            )
            await connection.execute(
                text(
                    "UPDATE sync_read_snapshot SET status = 'ready' "
                    "WHERE snapshot_id = :snapshot_id"
                ),
                {"snapshot_id": snapshot_id},
            )
            await connection.execute(
                text("""
                    INSERT INTO sync_read_snapshot_cursor (
                      tenant_id, branch_id, node_id, active_snapshot_id
                    ) VALUES (:tenant_id, :branch_id, :node_id, :snapshot_id)
                    """),
                {
                    "tenant_id": tenant_id,
                    "branch_id": branch_id,
                    "node_id": node_ids[0],
                    "snapshot_id": snapshot_id,
                },
            )
            assert (
                await connection.execute(text("SELECT count(*) FROM sync_read_snapshot"))
            ).scalar_one() == 1

            savepoint = await connection.begin_nested()
            try:
                with pytest.raises(DBAPIError) as error:
                    await connection.execute(
                        text("""
                            INSERT INTO sync_read_snapshot (
                              tenant_id, branch_id, node_id, schema_version,
                              source_revision, credential_kid, manifest_hash,
                              snapshot_hash, signature_algorithm, signature,
                              source_generated_at
                            ) VALUES (
                              :tenant_id, :branch_id, :node_id, 1, 2,
                              gen_random_uuid(), repeat('1', 64), repeat('2', 64),
                              'hmac-sha256-edge-read-snapshot-v1', repeat('3', 64), now()
                            )
                            """),
                        {
                            "tenant_id": tenant_id,
                            "branch_id": branch_id,
                            "node_id": node_ids[1],
                        },
                    )
                assert getattr(error.value.orig, "sqlstate", None) == "42501"
            finally:
                await savepoint.rollback()
        finally:
            await transaction.rollback()
