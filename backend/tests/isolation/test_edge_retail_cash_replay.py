"""Replay exposes a private, read-only surface under actual native identity."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

HELPER = "public.replay_edge_retail_cash_v1(uuid,uuid,bigint,uuid,jsonb,text)"
TABLES = ("pos_physical_checkout", "pos_mixed_checkout")
READ_COLUMNS = {
    "tenant_id",
    "id",
    "sale_id",
    "branch_id",
    "register_id",
    "shift_id",
    "cashier_user_id",
    "cash_intent_id",
    "payment_plan_intent_id",
    "stock_command_id",
    "payment_id",
    "payment_journal_version",
    "request_hash",
    "result_payload",
    "receipt_payload",
}


async def test_native_cash_replay_helper_is_private_invoker(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        row = (
            await connection.execute(
                text("""
                SELECT pg_get_userbyid(proowner),prosecdef,provolatile::TEXT,proconfig,
                  has_function_privilege('aurum_edge_cash_owner',oid,'EXECUTE'),
                  has_function_privilege('aurum_edge_cash_executor',oid,'EXECUTE'),
                  has_function_privilege('aurum_app',oid,'EXECUTE'),
                  has_function_privilege('aurum_support',oid,'EXECUTE'),
                  EXISTS(SELECT 1 FROM aclexplode(COALESCE(proacl,acldefault('f',proowner))) acl
                    WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE')
                FROM pg_proc WHERE oid=to_regprocedure(:helper)
            """),
                {"helper": HELPER},
            )
        ).one()
    assert tuple(row[:3]) == ("aurum_schema_owner", False, "v")
    assert set(row[3]) == {"search_path=pg_catalog, pg_temp", "row_security=on"}
    assert tuple(row[4:]) == (True, False, False, False, False)


async def test_native_cash_replay_reads_and_physical_writer_inserts_exact_columns(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        rows = (await connection.execute(text("""
                SELECT relation.relname,attribute.attname,
                  has_column_privilege('aurum_edge_cash_owner',relation.oid,
                    attribute.attnum,'SELECT') AS can_select,
                  has_column_privilege('aurum_edge_cash_owner',relation.oid,
                    attribute.attnum,'INSERT') AS can_insert,
                  has_column_privilege('aurum_edge_cash_owner',relation.oid,
                    attribute.attnum,'UPDATE,REFERENCES') AS can_change,
                  has_column_privilege('aurum_edge_cash_owner',relation.oid,
                    attribute.attnum,'SELECT WITH GRANT OPTION') AS grantable,
                  has_column_privilege('aurum_edge_cash_executor',relation.oid,
                    attribute.attnum,'SELECT,INSERT,UPDATE,REFERENCES') AS executor_access
                FROM pg_attribute attribute
                JOIN pg_class relation ON relation.oid=attribute.attrelid
                WHERE relation.oid IN ('public.pos_physical_checkout'::REGCLASS,
                  'public.pos_mixed_checkout'::REGCLASS)
                  AND attribute.attnum>0 AND NOT attribute.attisdropped
            """))).mappings().all()
        table_write = await connection.scalar(text("""
            SELECT bool_or(has_table_privilege('aurum_edge_cash_owner',oid,
              'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'))
            FROM pg_class WHERE oid IN ('public.pos_physical_checkout'::REGCLASS,
              'public.pos_mixed_checkout'::REGCLASS)
        """))
    for table in TABLES:
        scoped = [row for row in rows if row["relname"] == table]
        assert {row["attname"] for row in scoped if row["can_select"]} == READ_COLUMNS
        assert {row["attname"] for row in scoped if row["can_insert"]} == (
            {
                "tenant_id",
                "id",
                "sale_id",
                "branch_id",
                "register_id",
                "shift_id",
                "cashier_user_id",
                "cash_intent_id",
                "stock_command_id",
                "payment_id",
                "payment_journal_version",
                "request_hash",
                "result_payload",
                "receipt_payload",
                "created_by",
            }
            if table == "pos_physical_checkout"
            else set()
        )
    assert table_write is False
    assert all(
        not row["can_change"] and not row["grantable"] and not row["executor_access"]
        for row in rows
    )


async def test_native_cash_replay_rls_uses_actual_identity_and_cashier(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        rows = (await connection.execute(text("""
                SELECT relation.relname,relation.relrowsecurity,relation.relforcerowsecurity,
                  policy.polcmd::TEXT,policy.polpermissive,
                  policy.polroles=ARRAY[(SELECT oid FROM pg_roles
                    WHERE rolname='aurum_edge_cash_owner')] AS owner_only,
                  pg_get_expr(policy.polqual,policy.polrelid) AS expression,
                  pg_get_expr(policy.polwithcheck,policy.polrelid) AS check_expression
                FROM pg_policy policy JOIN pg_class relation ON relation.oid=policy.polrelid
                WHERE policy.polname='edge_cash_replay_read'
            """))).mappings().all()
    assert {row["relname"] for row in rows} == set(TABLES)
    for row in rows:
        assert row["relrowsecurity"] and row["relforcerowsecurity"]
        assert row["polcmd"] == "r" and row["polpermissive"] and row["owner_only"]
        assert row["check_expression"] is None
        for required in (
            "SESSION_USER",
            "database_role_oid",
            "tenant_id",
            "branch_id",
            "register_id",
            "cashier_user_id",
            "current_app_user_id",
        ):
            assert required in row["expression"]


@pytest.mark.parametrize("kind", ["physical", "mixed"])
async def test_native_replay_integrity_validator_is_not_publicly_callable(
    maintenance_engine: AsyncEngine,
    kind: str,
) -> None:
    async with maintenance_engine.connect() as connection:
        row = (
            await connection.execute(
                text("""
                SELECT pg_get_userbyid(proowner),prosecdef,
                  has_function_privilege('aurum_edge_cash_owner',oid,'EXECUTE'),
                  has_function_privilege('aurum_edge_cash_executor',oid,'EXECUTE'),
                  has_function_privilege('aurum_app',oid,'EXECUTE'),
                  has_function_privilege('aurum_support',oid,'EXECUTE'),
                  EXISTS(SELECT 1 FROM aclexplode(COALESCE(proacl,acldefault('f',proowner))) acl
                    WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE')
                FROM pg_proc WHERE oid=to_regprocedure(:validator)
            """),
                {"validator": f"public.assert_{kind}_checkout(uuid,uuid,boolean)"},
            )
        ).one()
    assert tuple(row) == ("aurum_schema_owner", True, True, False, False, False, False)


@pytest.mark.parametrize("role", ["aurum_app", "aurum_support"])
async def test_nonowner_cannot_invoke_native_cash_replay(role: str) -> None:
    settings = get_settings()
    url = settings.DATABASE_URL_APP if role == "aurum_app" else settings.DATABASE_URL_SUPPORT
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            assert await connection.scalar(text("SELECT session_user")) == role
            with pytest.raises(DBAPIError) as denied:
                async with connection.begin_nested():
                    await connection.execute(
                        text(
                            "SELECT public.replay_edge_retail_cash_v1("
                            ":operation,:activation,1,:actor,'{}'::JSONB,:hash)"
                        ),
                        {
                            "operation": uuid4(),
                            "activation": uuid4(),
                            "actor": uuid4(),
                            "hash": "a" * 64,
                        },
                    )
        assert getattr(denied.value.orig, "sqlstate", None) == "42501"
    finally:
        await engine.dispose()


async def test_native_replay_rejects_forged_gucs_without_actual_node_identity(
    maintenance_engine: AsyncEngine,
) -> None:
    scope: dict[str, object] = {
        key: str(uuid4())
        for key in (
            "activation_id",
            "tenant_id",
            "branch_id",
            "edge_node_id",
            "register_id",
            "user_id",
        )
    }
    scope.update(writer_epoch=1, capability="cash_sale_v1")
    operation = uuid4()
    payload = json.dumps(
        {
            "profile": "retail_cash.replay.v1",
            "kind": "physical_cash",
            "scope": scope,
            "confirmation": {
                "schema_version": 2,
                "operation_id": str(operation),
                "sale_id": str(uuid4()),
                "expected_revision_hash": "a" * 64,
                "expected_total_amount": "10.00",
                "cash_tendered": "10.00",
                "currency": "TJS",
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            assert str(await connection.scalar(text("SELECT current_database()"))).endswith("_test")
            assert await connection.scalar(text("SELECT session_user")) == "aurum_migrator"
            await connection.execute(text("RESET ROLE"))
            await connection.execute(text("SET LOCAL ROLE aurum_edge_cash_owner"))
            for key in ("tenant_id", "branch_id", "edge_node_id", "user_id"):
                await connection.execute(
                    text("SELECT set_config(:name,:value,true)"),
                    {"name": f"app.{key}", "value": scope[key]},
                )
            with pytest.raises(DBAPIError) as denied:
                async with connection.begin_nested():
                    await connection.execute(
                        text(
                            "SELECT public.replay_edge_retail_cash_v1("
                            ":operation,:activation,1,:actor,CAST(:payload AS JSONB),:hash)"
                        ),
                        {
                            "operation": operation,
                            "activation": UUID(str(scope["activation_id"])),
                            "actor": UUID(str(scope["user_id"])),
                            "payload": payload,
                            "hash": hashlib.sha256(payload.encode()).hexdigest(),
                        },
                    )
            assert getattr(denied.value.orig, "sqlstate", None) == "42501"
        finally:
            await transaction.rollback()
