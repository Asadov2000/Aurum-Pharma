"""Database contract for the fail-closed Edge cash dispatcher."""

from __future__ import annotations

import json
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.domains.pos.service import NormalizedCheckoutPayment, POSService
from app.domains.sync.integrity import canonical_json_hash


async def test_edge_cash_dispatcher_has_a_narrow_security_boundary(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        dispatcher = (await connection.execute(text("""
                    SELECT
                      pg_catalog.pg_get_userbyid(routine.proowner) AS owner,
                      language.lanname AS language,
                      routine.prosecdef,
                      routine.provolatile::TEXT AS provolatile,
                      routine.proconfig,
                      pg_catalog.has_function_privilege(
                        'aurum_edge_cash_executor', routine.oid, 'EXECUTE'
                      ) AS executor_can_execute,
                      pg_catalog.has_function_privilege(
                        'aurum_app', routine.oid, 'EXECUTE'
                      ) AS app_can_execute,
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
                      ) AS public_can_execute
                    FROM pg_catalog.pg_proc AS routine
                    JOIN pg_catalog.pg_language AS language
                      ON language.oid = routine.prolang
                    WHERE routine.oid = pg_catalog.to_regprocedure(
                      'public.dispatch_edge_cash_sale_v1('
                      'uuid,uuid,bigint,uuid,jsonb,text)'
                    )
                    """))).mappings().one()
        executor_table_access = await connection.scalar(text("""
                SELECT pg_catalog.bool_or(
                  pg_catalog.has_table_privilege(
                    'aurum_edge_cash_executor', relation.oid, privilege.name
                  )
                )
                FROM pg_catalog.pg_class AS relation
                JOIN pg_catalog.pg_namespace AS schema
                  ON schema.oid = relation.relnamespace
                CROSS JOIN (VALUES
                  ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE'),
                  ('TRUNCATE'), ('REFERENCES'), ('TRIGGER')
                ) AS privilege(name)
                WHERE schema.nspname = 'public'
                  AND relation.relkind IN ('r', 'p')
                """))
        unsafe_owner_privileges = (await connection.execute(text("""
                    SELECT relation.relname, privilege.name
                    FROM pg_catalog.pg_class AS relation
                    JOIN pg_catalog.pg_namespace AS schema
                      ON schema.oid = relation.relnamespace
                    CROSS JOIN (VALUES
                      ('DELETE'), ('TRUNCATE'), ('REFERENCES'), ('TRIGGER')
                    ) AS privilege(name)
                    WHERE schema.nspname = 'public'
                      AND relation.relkind IN ('r', 'p')
                      AND pg_catalog.has_table_privilege(
                        'aurum_edge_cash_owner', relation.oid, privilege.name
                      )
                    ORDER BY relation.relname, privilege.name
                    """))).all()

    assert dict(dispatcher) == {
        "owner": "aurum_edge_cash_owner",
        "language": "plpgsql",
        "prosecdef": True,
        "provolatile": "v",
        "proconfig": ["search_path=pg_catalog, pg_temp", "row_security=on"],
        "executor_can_execute": True,
        "app_can_execute": False,
        "support_can_execute": False,
        "public_can_execute": False,
    }
    assert executor_table_access is False
    assert unsafe_owner_privileges == []


async def test_edge_cash_dispatcher_rls_policies_are_explicit(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        policies = (await connection.execute(text("""
                    SELECT tablename, policyname, roles, cmd
                    FROM pg_catalog.pg_policies
                    WHERE schemaname = 'public'
                      AND policyname IN (
                        'edge_cash_dispatcher_owner',
                        'edge_cash_dispatcher_cashier_read',
                        'edge_cash_writer_guard_identity'
                      )
                    ORDER BY tablename, policyname
                    """))).mappings()

    assert [dict(policy) for policy in policies] == [
        {
            "tablename": "app_user",
            "policyname": "edge_cash_dispatcher_cashier_read",
            "roles": ["aurum_edge_cash_owner"],
            "cmd": "SELECT",
        },
        {
            "tablename": "edge_cash_command",
            "policyname": "edge_cash_dispatcher_owner",
            "roles": ["aurum_edge_cash_owner"],
            "cmd": "ALL",
        },
        {
            "tablename": "edge_cash_node_identity",
            "policyname": "edge_cash_dispatcher_owner",
            "roles": ["aurum_edge_cash_owner"],
            "cmd": "ALL",
        },
        {
            "tablename": "edge_cash_node_identity",
            "policyname": "edge_cash_writer_guard_identity",
            "roles": ["aurum_schema_owner"],
            "cmd": "SELECT",
        },
        {
            "tablename": "tenant_membership",
            "policyname": "edge_cash_dispatcher_cashier_read",
            "roles": ["aurum_edge_cash_owner"],
            "cmd": "SELECT",
        },
    ]


async def test_database_canonical_json_matches_application_hashes(
    maintenance_engine: AsyncEngine,
) -> None:
    register_id = uuid4()
    catalog_id = uuid4()
    nested_payload = {
        "z": [3, {"medicine": "Парацетамол"}],
        "a": {"enabled": True, "value": None},
    }
    payments: list[NormalizedCheckoutPayment] = [("cash", Decimal("20.00"), None, None)]
    expected_checkout_hash = POSService._checkout_operation_hash(
        register_id=register_id,
        draft_sale_id=None,
        items=[(catalog_id, Decimal("2.000"))],
        payments=payments,
        prescription=None,
    )

    async with maintenance_engine.connect() as connection:
        nested_hash = await connection.scalar(
            text("""
                SELECT pg_catalog.encode(
                  pg_catalog.sha256(pg_catalog.convert_to(
                    public.edge_canonical_jsonb(CAST(:payload AS JSONB)),
                    'UTF8'
                  )),
                  'hex'
                )
                """),
            {"payload": json.dumps(nested_payload, ensure_ascii=False)},
        )
        checkout_hash = await connection.scalar(
            text("""
                SELECT pg_catalog.encode(
                  pg_catalog.sha256(pg_catalog.convert_to(
                    public.edge_canonical_jsonb(pg_catalog.jsonb_build_object(
                      'draft_sale_id', NULL,
                      'items', pg_catalog.jsonb_build_array(
                        pg_catalog.jsonb_build_array(
                          CAST(:catalog_id AS TEXT), '2'
                        )
                      ),
                      'kind', 'sale_checkout_v1',
                      'payments', pg_catalog.jsonb_build_array(
                        pg_catalog.jsonb_build_object(
                          'amount', '20',
                          'metadata', NULL,
                          'payment_method', 'cash'
                        )
                      ),
                      'prescription', NULL,
                      'register_id', CAST(:register_id AS TEXT)
                    )),
                    'UTF8'
                  )),
                  'hex'
                )
                """),
            {
                "catalog_id": str(catalog_id),
                "register_id": str(register_id),
            },
        )

    assert nested_hash == canonical_json_hash(nested_payload)
    assert checkout_hash == expected_checkout_hash
