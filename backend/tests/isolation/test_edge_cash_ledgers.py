"""Isolation contract for the Edge cash security ledgers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.sql.elements import TextClause


@dataclass(frozen=True, slots=True)
class _LedgerScaffold:
    tenant_id: UUID
    user_id: UUID
    identity_id: UUID
    command_id: UUID
    operation_id: UUID
    sale_id: UUID


async def _assert_role_identity_rejected(
    connection: AsyncConnection,
    identity_insert: TextClause,
    identity_params: dict[str, object],
) -> None:
    with pytest.raises(DBAPIError, match="role identity does not match"):
        async with connection.begin_nested():
            await connection.execute(identity_insert, identity_params)


async def _assert_command_constraints(
    connection: AsyncConnection,
    command_insert: TextClause,
    command_params: dict[str, object],
    result_payload: dict[str, str],
) -> None:
    invalid_payload = {**result_payload, "receipt_number": "EDGE-MISMATCH"}
    with pytest.raises(DBAPIError) as payload_error:
        async with connection.begin_nested():
            await connection.execute(
                command_insert,
                {
                    **command_params,
                    "result_payload": json.dumps(invalid_payload, separators=(",", ":")),
                },
            )
    assert getattr(payload_error.value.orig, "sqlstate", None) == "23514"

    scope_mutations: tuple[tuple[str, object], ...] = (
        ("tenant_id", uuid4()),
        ("branch_id", uuid4()),
        ("identity_id", uuid4()),
        ("activation_id", uuid4()),
        ("edge_node_id", uuid4()),
        ("writer_epoch", 999),
        ("register_id", uuid4()),
        ("user_id", uuid4()),
        ("operation_id", uuid4()),
        ("sale_id", uuid4()),
        ("receipt_number", "EDGE-OTHER"),
        ("total_amount", Decimal("1.00")),
    )
    for field, invalid_value in scope_mutations:
        invalid_params = {**command_params, field: invalid_value}
        invalid_result = dict(result_payload)
        if field in {"operation_id", "sale_id"}:
            invalid_result[field] = str(invalid_value)
        elif field in {"receipt_number", "total_amount"}:
            invalid_result[field] = str(invalid_value)
        invalid_params["result_payload"] = json.dumps(invalid_result, separators=(",", ":"))
        with pytest.raises(DBAPIError) as scope_error:
            async with connection.begin_nested():
                await connection.execute(command_insert, invalid_params)
        assert getattr(scope_error.value.orig, "sqlstate", None) == "23503"


async def test_edge_cash_ledgers_are_dispatcher_scoped_and_force_rls(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        tables = (await connection.execute(text("""
                SELECT
                  relations.relname,
                  relations.relrowsecurity,
                  relations.relforcerowsecurity,
                  pg_catalog.pg_get_userbyid(relations.relowner) AS owner,
                  count(policies.policyname) AS policy_count
                FROM pg_catalog.pg_class AS relations
                JOIN pg_catalog.pg_namespace AS schemas
                  ON schemas.oid = relations.relnamespace
                LEFT JOIN pg_catalog.pg_policies AS policies
                  ON policies.schemaname = schemas.nspname
                 AND policies.tablename = relations.relname
                WHERE schemas.nspname = 'public'
                  AND relations.relname IN (
                    'edge_cash_node_identity', 'edge_cash_command'
                  )
                GROUP BY relations.oid
                ORDER BY relations.relname
                """))).mappings()
        privileges = (await connection.execute(text("""
                SELECT
                  roles.role_name,
                  tables.table_name,
                  bool_or(pg_catalog.has_table_privilege(
                    roles.role_name,
                    pg_catalog.format('public.%I', tables.table_name),
                    privileges.privilege
                  )) AS has_privilege
                FROM (VALUES
                  ('aurum_app'),
                  ('aurum_support'),
                  ('aurum_edge_cash_executor'),
                  ('aurum_edge_cash_owner')
                ) AS roles(role_name)
                CROSS JOIN (VALUES
                  ('edge_cash_node_identity'), ('edge_cash_command')
                ) AS tables(table_name)
                CROSS JOIN (VALUES
                  ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE'),
                  ('TRUNCATE'), ('REFERENCES'), ('TRIGGER')
                ) AS privileges(privilege)
                GROUP BY roles.role_name, tables.table_name
                ORDER BY roles.role_name, tables.table_name
                """))).mappings()
        public_privileges = (await connection.execute(text("""
                SELECT relations.relname
                FROM pg_catalog.pg_class AS relations
                JOIN pg_catalog.pg_namespace AS schemas
                  ON schemas.oid = relations.relnamespace
                CROSS JOIN LATERAL pg_catalog.aclexplode(
                  COALESCE(
                    relations.relacl,
                    pg_catalog.acldefault('r'::"char", relations.relowner)
                  )
                ) AS privileges
                WHERE schemas.nspname = 'public'
                  AND relations.relname IN (
                    'edge_cash_node_identity', 'edge_cash_command'
                  )
                  AND privileges.grantee = 0
                """))).all()
        triggers = (await connection.execute(text("""
                SELECT event_object_table, trigger_name
                FROM information_schema.triggers
                WHERE event_object_schema = 'public'
                  AND event_object_table IN (
                    'edge_cash_node_identity', 'edge_cash_command'
                  )
                GROUP BY event_object_table, trigger_name
                ORDER BY event_object_table, trigger_name
                """))).mappings()
        functions = (await connection.execute(text("""
                SELECT
                  routines.proname,
                  routines.prosecdef,
                  routines.proconfig,
                  pg_catalog.pg_get_userbyid(routines.proowner) AS owner,
                  pg_catalog.has_function_privilege(
                    'aurum_support', routines.oid, 'EXECUTE'
                  ) AS support_can_execute,
                  pg_catalog.has_function_privilege(
                    'aurum_edge_cash_executor', routines.oid, 'EXECUTE'
                  ) AS executor_can_execute,
                  pg_catalog.has_function_privilege(
                    'aurum_edge_cash_owner', routines.oid, 'EXECUTE'
                  ) AS edge_owner_can_execute,
                  EXISTS (
                    SELECT 1
                    FROM pg_catalog.aclexplode(
                      COALESCE(
                        routines.proacl,
                        pg_catalog.acldefault('f'::"char", routines.proowner)
                      )
                    ) AS privileges
                    WHERE privileges.grantee = 0
                  ) AS public_can_execute
                FROM pg_catalog.pg_proc AS routines
                JOIN pg_catalog.pg_namespace AS schemas
                  ON schemas.oid = routines.pronamespace
                WHERE schemas.nspname = 'public'
                  AND routines.proname IN (
                    'trg_audit_edge_cash_command_insert',
                    'trg_guard_edge_cash_append_only',
                    'trg_validate_edge_cash_node_identity'
                  )
                ORDER BY routines.proname
                """))).mappings()

    assert [dict(row) for row in tables] == [
        {
            "relname": "edge_cash_command",
            "relrowsecurity": True,
            "relforcerowsecurity": True,
            "owner": "aurum_schema_owner",
            "policy_count": 1,
        },
        {
            "relname": "edge_cash_node_identity",
            "relrowsecurity": True,
            "relforcerowsecurity": True,
            "owner": "aurum_schema_owner",
            "policy_count": 2,
        },
    ]
    privilege_map = {
        (str(row["role_name"]), str(row["table_name"])): bool(row["has_privilege"])
        for row in privileges
    }
    assert privilege_map == {
        ("aurum_app", "edge_cash_command"): False,
        ("aurum_app", "edge_cash_node_identity"): False,
        ("aurum_edge_cash_executor", "edge_cash_command"): False,
        ("aurum_edge_cash_executor", "edge_cash_node_identity"): False,
        ("aurum_edge_cash_owner", "edge_cash_command"): True,
        ("aurum_edge_cash_owner", "edge_cash_node_identity"): True,
        ("aurum_support", "edge_cash_command"): False,
        ("aurum_support", "edge_cash_node_identity"): False,
    }
    assert public_privileges == []
    assert [dict(row) for row in triggers] == [
        {
            "event_object_table": "edge_cash_command",
            "trigger_name": "trg_audit_edge_cash_command",
        },
        {
            "event_object_table": "edge_cash_command",
            "trigger_name": "trg_edge_cash_command_created",
        },
        {
            "event_object_table": "edge_cash_command",
            "trigger_name": "trg_edge_cash_command_immutable",
        },
        {
            "event_object_table": "edge_cash_node_identity",
            "trigger_name": "trg_audit_edge_cash_node_identity",
        },
        {
            "event_object_table": "edge_cash_node_identity",
            "trigger_name": "trg_edge_cash_node_identity_created",
        },
        {
            "event_object_table": "edge_cash_node_identity",
            "trigger_name": "trg_edge_cash_node_identity_immutable",
        },
        {
            "event_object_table": "edge_cash_node_identity",
            "trigger_name": "trg_edge_cash_node_identity_validate",
        },
    ]
    function_rows = [dict(row) for row in functions]
    assert [row["proname"] for row in function_rows] == [
        "trg_audit_edge_cash_command_insert",
        "trg_guard_edge_cash_append_only",
        "trg_validate_edge_cash_node_identity",
    ]
    assert all(row["owner"] == "aurum_schema_owner" for row in function_rows)
    assert all(not row["support_can_execute"] for row in function_rows)
    assert all(not row["executor_can_execute"] for row in function_rows)
    assert all(not row["edge_owner_can_execute"] for row in function_rows)
    assert all(not row["public_can_execute"] for row in function_rows)
    assert function_rows[0]["prosecdef"] is True
    assert function_rows[0]["proconfig"] == ["search_path=pg_catalog, pg_temp"]
    assert function_rows[1]["prosecdef"] is False
    assert function_rows[1]["proconfig"] == ["search_path=pg_catalog"]
    assert function_rows[2]["prosecdef"] is True
    assert function_rows[2]["proconfig"] == ["search_path=pg_catalog, pg_temp"]


async def _create_scaffold(connection: AsyncConnection) -> _LedgerScaffold:
    suffix = uuid4().hex
    cloud_activation_id = uuid4()
    edge_activation_id = uuid4()
    operation_id = uuid4()
    request_hash = "a" * 64
    result_hash = "b" * 64
    zero_hash = "0" * 64
    receipt_number = f"EDGE-{suffix[:12]}"

    async with connection.begin_nested():
        tenant_id = UUID(
            str(
                await connection.scalar(
                    text("""
                INSERT INTO public.tenant (name, contact_email, status)
                VALUES (:name, :email, 'active')
                RETURNING id
                """),
                    {"name": f"Edge ledger {suffix[:8]}", "email": f"edge-{suffix}@aurum.tj"},
                )
            )
        )
        user_id = UUID(
            str(
                await connection.scalar(
                    text("""
                INSERT INTO public.app_user (
                  email, full_name, home_tenant_id, status
                ) VALUES (:email, 'Edge cashier', :tenant_id, 'active')
                RETURNING id
                """),
                    {"email": f"edge-user-{suffix}@aurum.tj", "tenant_id": tenant_id},
                )
            )
        )
        await connection.execute(text("""
                ALTER TABLE public.branch
                DISABLE TRIGGER trg_branch_sync_writer
                """))
        try:
            branch_id = UUID(
                str(
                    await connection.scalar(
                        text("""
                    INSERT INTO public.branch (tenant_id, name)
                    VALUES (:tenant_id, 'Edge branch') RETURNING id
                    """),
                        {"tenant_id": tenant_id},
                    )
                )
            )
        finally:
            await connection.execute(text("""
                    ALTER TABLE public.branch
                    ENABLE TRIGGER trg_branch_sync_writer
                    """))
        register_id = UUID(
            str(
                await connection.scalar(
                    text("""
                INSERT INTO public.register (tenant_id, branch_id, name)
                VALUES (:tenant_id, :branch_id, 'Edge register') RETURNING id
                """),
                    {"tenant_id": tenant_id, "branch_id": branch_id},
                )
            )
        )
        cloud_node_id = UUID(
            str(
                await connection.scalar(
                    text("""
                INSERT INTO public.sync_node (
                  tenant_id, branch_id, node_kind, mode, status, display_name
                ) VALUES (
                  :tenant_id, :branch_id, 'cloud', 'cloud_writer',
                  'active', 'Cloud writer'
                ) RETURNING id
                """),
                    {"tenant_id": tenant_id, "branch_id": branch_id},
                )
            )
        )
        await connection.execute(
            text("""
                INSERT INTO public.sync_writer_epoch (
                  tenant_id, branch_id, writer_epoch, activation_id,
                  writer_node_id, allowed_register_id, capability, state,
                  root_source_checksum, root_projection_checksum, last_sequence,
                  current_source_checksum, current_projection_checksum,
                  previous_writer_epoch, previous_terminal_sequence,
                  previous_terminal_source_checksum,
                  previous_terminal_projection_checksum,
                  bootstrap_snapshot_hash, activation_manifest_hash,
                  receipt_baseline_seq, prepared_at, activated_at, fenced_at
                ) VALUES (
                  :tenant_id, :branch_id, 1, :activation_id,
                  :cloud_node_id, NULL, 'cloud_full', 'fenced',
                  :zero_hash, :zero_hash, 0, :zero_hash, :zero_hash,
                  NULL, NULL, NULL, NULL,
                  :zero_hash, :zero_hash, 0, now(), now(), now()
                )
                """),
            {
                "tenant_id": tenant_id,
                "branch_id": branch_id,
                "activation_id": cloud_activation_id,
                "cloud_node_id": cloud_node_id,
                "zero_hash": zero_hash,
            },
        )
        edge_node_id = UUID(
            str(
                await connection.scalar(
                    text("""
                INSERT INTO public.sync_node (
                  tenant_id, branch_id, register_id, node_kind, mode, status,
                  display_name, credential_kid, credential_hash,
                  credential_issued_at, credential_expires_at,
                  shadow_start_origin_node_id, shadow_start_writer_epoch,
                  shadow_start_sequence, shadow_start_checksum,
                  shadow_start_projection_checksum
                ) VALUES (
                  :tenant_id, :branch_id, :register_id, 'edge', 'edge_writer',
                  'active', 'Edge writer', :credential_kid, :credential_hash,
                  now(), now() + interval '1 day', :cloud_node_id, 1, 0,
                  :zero_hash, :zero_hash
                ) RETURNING id
                """),
                    {
                        "tenant_id": tenant_id,
                        "branch_id": branch_id,
                        "register_id": register_id,
                        "credential_kid": uuid4(),
                        "credential_hash": suffix + suffix,
                        "cloud_node_id": cloud_node_id,
                        "zero_hash": zero_hash,
                    },
                )
            )
        )
        await connection.execute(
            text("""
                INSERT INTO public.sync_writer_activation (
                  activation_id, tenant_id, branch_id, writer_epoch,
                  writer_node_id, allowed_register_id, capability, state,
                  root_source_checksum, root_projection_checksum, last_sequence,
                  current_source_checksum, current_projection_checksum,
                  previous_writer_epoch, previous_terminal_sequence,
                  previous_terminal_source_checksum,
                  previous_terminal_projection_checksum,
                  bootstrap_snapshot_hash, activation_manifest_hash,
                  receipt_baseline_seq, prepare_request_hash,
                  prepared_at, ready_at, activated_at
                ) VALUES (
                  :activation_id, :tenant_id, :branch_id, 2,
                  :edge_node_id, :register_id, 'cash_sale_v1', 'activated',
                  :zero_hash, :zero_hash, 0, :zero_hash, :zero_hash,
                  1, 0, :zero_hash, :zero_hash,
                  :zero_hash, :zero_hash, 0, :request_hash,
                  now(), now(), now()
                )
                """),
            {
                "activation_id": edge_activation_id,
                "tenant_id": tenant_id,
                "branch_id": branch_id,
                "edge_node_id": edge_node_id,
                "register_id": register_id,
                "zero_hash": zero_hash,
                "request_hash": "d" * 64,
            },
        )
        await connection.execute(
            text("""
                INSERT INTO public.sync_writer_epoch (
                  tenant_id, branch_id, writer_epoch, activation_id,
                  writer_node_id, allowed_register_id, capability, state,
                  root_source_checksum, root_projection_checksum, last_sequence,
                  current_source_checksum, current_projection_checksum,
                  previous_writer_epoch, previous_terminal_sequence,
                  previous_terminal_source_checksum,
                  previous_terminal_projection_checksum,
                  bootstrap_snapshot_hash, activation_manifest_hash,
                  receipt_baseline_seq, prepared_at, activated_at
                ) VALUES (
                  :tenant_id, :branch_id, 2, :activation_id,
                  :edge_node_id, :register_id, 'cash_sale_v1', 'active',
                  :zero_hash, :zero_hash, 0, :zero_hash, :zero_hash,
                  1, 0, :zero_hash, :zero_hash,
                  :zero_hash, :zero_hash, 0, now(), now()
                )
                """),
            {
                "tenant_id": tenant_id,
                "branch_id": branch_id,
                "activation_id": edge_activation_id,
                "edge_node_id": edge_node_id,
                "register_id": register_id,
                "zero_hash": zero_hash,
            },
        )
        identity_insert = text("""
                INSERT INTO public.edge_cash_node_identity (
                  tenant_id, branch_id, edge_node_id, register_id,
                  database_role, database_role_oid
                ) VALUES (
                  :tenant_id, :branch_id, :edge_node_id, :register_id,
                  :database_role,
                  (SELECT oid FROM pg_catalog.pg_roles
                   WHERE rolname = 'aurum_edge_cash_executor')
                ) RETURNING id
                """)
        identity_params = {
            "tenant_id": tenant_id,
            "branch_id": branch_id,
            "edge_node_id": edge_node_id,
            "register_id": register_id,
            "database_role": f"aurum_edge_node_{edge_node_id.hex}",
        }
        await _assert_role_identity_rejected(connection, identity_insert, identity_params)

        await connection.execute(text("""
            ALTER TABLE public.edge_cash_node_identity
              DISABLE TRIGGER trg_edge_cash_node_identity_validate
            """))
        try:
            identity_id = UUID(str(await connection.scalar(identity_insert, identity_params)))
        finally:
            await connection.execute(text("""
                ALTER TABLE public.edge_cash_node_identity
                  ENABLE TRIGGER trg_edge_cash_node_identity_validate
                """))
        await connection.execute(
            text("ALTER TABLE public.shift DISABLE TRIGGER trg_shift_writer_guard")
        )
        await connection.execute(
            text("ALTER TABLE public.sale DISABLE TRIGGER trg_sale_writer_guard")
        )
        await connection.execute(
            text("ALTER TABLE public.sale DISABLE TRIGGER trg_guard_sale_immutability")
        )
        try:
            shift_id = UUID(
                str(
                    await connection.scalar(
                        text("""
                    INSERT INTO public.shift (
                      tenant_id, branch_id, register_id, opened_by_user_id, status
                    ) VALUES (
                      :tenant_id, :branch_id, :register_id, :user_id, 'open'
                    ) RETURNING id
                    """),
                        {
                            "tenant_id": tenant_id,
                            "branch_id": branch_id,
                            "register_id": register_id,
                            "user_id": user_id,
                        },
                    )
                )
            )
            sale_id = UUID(
                str(
                    await connection.scalar(
                        text("""
                    INSERT INTO public.sale (
                      tenant_id, branch_id, register_id, shift_id,
                      cashier_user_id, operation_id, operation_hash,
                      status, receipt_number, receipt_seq, completed_at,
                      receipt_snapshot
                    ) VALUES (
                      :tenant_id, :branch_id, :register_id, :shift_id,
                      :user_id, :operation_id, :request_hash,
                      'completed', :receipt_number, 1, now(), '{}'::jsonb
                    ) RETURNING id
                    """),
                        {
                            "tenant_id": tenant_id,
                            "branch_id": branch_id,
                            "register_id": register_id,
                            "shift_id": shift_id,
                            "user_id": user_id,
                            "operation_id": operation_id,
                            "request_hash": request_hash,
                            "receipt_number": receipt_number,
                        },
                    )
                )
            )
        finally:
            await connection.execute(
                text("ALTER TABLE public.sale ENABLE TRIGGER trg_guard_sale_immutability")
            )
            await connection.execute(
                text("ALTER TABLE public.sale ENABLE TRIGGER trg_sale_writer_guard")
            )
            await connection.execute(
                text("ALTER TABLE public.shift ENABLE TRIGGER trg_shift_writer_guard")
            )
        result_payload = {
            "command_type": "sale.cash.complete",
            "operation_id": str(operation_id),
            "sale_id": str(sale_id),
            "receipt_number": receipt_number,
            "total_amount": "0.00",
            "currency": "TJS",
        }
        command_insert = text("""
                INSERT INTO public.edge_cash_command (
                  tenant_id, branch_id, operation_id, edge_identity_id,
                  activation_id, edge_node_id, writer_epoch, register_id,
                  cashier_user_id, sale_id, receipt_number, total_amount,
                  currency, request_hash,
                  result_payload, result_hash
                ) VALUES (
                  :tenant_id, :branch_id, :operation_id, :identity_id,
                  :activation_id, :edge_node_id, :writer_epoch, :register_id,
                  :user_id, :sale_id, :receipt_number, :total_amount,
                  :currency, :request_hash,
                  CAST(:result_payload AS JSONB), :result_hash
                ) RETURNING id
                """)
        command_params: dict[str, object] = {
            "tenant_id": tenant_id,
            "branch_id": branch_id,
            "operation_id": operation_id,
            "identity_id": identity_id,
            "activation_id": edge_activation_id,
            "edge_node_id": edge_node_id,
            "writer_epoch": 2,
            "register_id": register_id,
            "user_id": user_id,
            "sale_id": sale_id,
            "receipt_number": receipt_number,
            "total_amount": Decimal("0.00"),
            "currency": "TJS",
            "request_hash": request_hash,
            "result_payload": json.dumps(result_payload, separators=(",", ":")),
            "result_hash": result_hash,
        }

        await _assert_command_constraints(
            connection, command_insert, command_params, result_payload
        )

        command_id = UUID(str(await connection.scalar(command_insert, command_params)))

    return _LedgerScaffold(
        tenant_id=tenant_id,
        user_id=user_id,
        identity_id=identity_id,
        command_id=command_id,
        operation_id=operation_id,
        sale_id=sale_id,
    )


async def test_edge_cash_ledgers_are_append_only_and_audit_hashes_only(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            scaffold = await _create_scaffold(connection)
            audit = (
                await connection.execute(
                    text("""
                        SELECT new_values
                        FROM public.audit_log
                        WHERE tenant_id = :tenant_id
                          AND table_name = 'edge_cash_command'
                          AND record_id = :command_id
                        """),
                    {
                        "tenant_id": scaffold.tenant_id,
                        "command_id": scaffold.command_id,
                    },
                )
            ).scalar_one()
            assert "result_payload" not in audit
            assert "receipt_number" not in audit
            assert "total_amount" not in audit
            assert "currency" not in audit
            assert audit["operation_id"] == str(scaffold.operation_id)
            assert audit["request_hash"] == "a" * 64
            assert audit["result_hash"] == "b" * 64
            assert audit["sale_status"] == "completed"

            immutable_writes = (
                (
                    "UPDATE public.edge_cash_command "
                    "SET result_hash = repeat('c', 64) WHERE id = :record_id",
                    scaffold.command_id,
                ),
                (
                    "DELETE FROM public.edge_cash_command WHERE id = :record_id",
                    scaffold.command_id,
                ),
                (
                    "UPDATE public.edge_cash_node_identity "
                    "SET database_role_oid = database_role_oid WHERE id = :record_id",
                    scaffold.identity_id,
                ),
                (
                    "DELETE FROM public.edge_cash_node_identity WHERE id = :record_id",
                    scaffold.identity_id,
                ),
            )
            for statement, record_id in immutable_writes:
                with pytest.raises(DBAPIError, match="append-only"):
                    async with connection.begin_nested():
                        await connection.execute(
                            text(statement),
                            {"record_id": record_id},
                        )
        finally:
            await transaction.rollback()
