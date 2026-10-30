"""The native preparation helper adds no executor table access or definer privilege."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def test_native_retail_preparation_helper_is_private_and_invoker(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        routine = (await connection.execute(text("""
                    SELECT routine.prosecdef, routine.provolatile::TEXT AS volatility,
                      routine.proconfig,
                      pg_catalog.has_function_privilege('aurum_edge_cash_owner',routine.oid,
                        'EXECUTE') AS owner_can_execute,
                      EXISTS(SELECT 1 FROM pg_catalog.aclexplode(routine.proacl) acl
                        WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE') AS public_execute,
                      pg_catalog.has_function_privilege('aurum_edge_cash_executor',routine.oid,
                        'EXECUTE') AS executor_execute,
                      pg_catalog.has_function_privilege('aurum_app',routine.oid,
                        'EXECUTE') AS app_execute,
                      pg_catalog.has_function_privilege('aurum_support',routine.oid,
                        'EXECUTE') AS support_execute
                    FROM pg_catalog.pg_proc routine
                    WHERE routine.oid=
                      'public.prepare_edge_retail_cash_v1(uuid,uuid,bigint,uuid,jsonb,text)'
                      ::REGPROCEDURE
                """))).mappings().one()
    assert routine["prosecdef"] is False
    assert routine["volatility"] == "v"
    assert set(routine["proconfig"]) == {"search_path=pg_catalog, pg_temp", "row_security=on"}
    assert routine["owner_can_execute"] is True
    assert all(
        routine[key] is False
        for key in ("public_execute", "executor_execute", "app_execute", "support_execute")
    )


async def test_native_retail_confirmation_has_only_explicit_read_columns(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        columns = (await connection.execute(text("""
                    SELECT column_row.attname,
                      pg_catalog.has_column_privilege('aurum_edge_cash_owner',
                        column_row.attrelid,column_row.attnum,'SELECT') AS can_select,
                      pg_catalog.has_column_privilege('aurum_edge_cash_owner',
                        column_row.attrelid,column_row.attnum,'INSERT') AS can_insert,
                      pg_catalog.has_column_privilege('aurum_edge_cash_owner',
                        column_row.attrelid,column_row.attnum,'UPDATE') AS can_update,
                      pg_catalog.has_column_privilege('aurum_edge_cash_executor',
                        column_row.attrelid,column_row.attnum,'SELECT') AS executor_select
                    FROM pg_catalog.pg_attribute column_row
                    WHERE column_row.attrelid='public.pos_unit_cash_intent'::REGCLASS
                      AND column_row.attnum>0 AND NOT column_row.attisdropped
                """))).mappings().all()
        table_write = await connection.scalar(text("""
                SELECT pg_catalog.has_table_privilege('aurum_edge_cash_owner',
                  'public.pos_unit_cash_intent','INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES')
            """))
    assert {row["attname"] for row in columns if row["can_select"]} == {
        "tenant_id",
        "id",
        "branch_id",
        "sale_id",
        "register_id",
        "shift_id",
        "cashier_user_id",
        "revision_hash",
        "expected_total_amount",
        "cash_tendered",
        "currency",
        "quote_payload",
    }
    assert table_write is False
    assert all(
        not row["can_insert"] and not row["can_update"] and not row["executor_select"]
        for row in columns
    )
