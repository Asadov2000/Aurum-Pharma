"""Database guards for customer-return quarantine journals."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def test_customer_return_journals_are_tenant_scoped_and_append_only(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        rows = (
            await connection.execute(
                text("""
                    SELECT
                      relations.relname AS table_name,
                      relations.relrowsecurity AS rls_enabled,
                      relations.relforcerowsecurity AS rls_forced,
                      pg_catalog.has_table_privilege(
                        'aurum_app', relations.oid, 'SELECT'
                      ) AS app_select,
                      pg_catalog.has_table_privilege(
                        'aurum_app', relations.oid, 'INSERT'
                      ) AS app_insert,
                      pg_catalog.has_table_privilege(
                        'aurum_app', relations.oid, 'UPDATE'
                      ) AS app_update,
                      pg_catalog.has_table_privilege(
                        'aurum_app', relations.oid, 'DELETE'
                      ) AS app_delete
                    FROM pg_catalog.pg_class AS relations
                    JOIN pg_catalog.pg_namespace AS namespaces
                      ON namespaces.oid = relations.relnamespace
                    WHERE namespaces.nspname = 'public'
                      AND relations.relname IN (
                        'customer_return_quarantine_item',
                        'customer_return_disposition'
                      )
                    ORDER BY relations.relname
                """)
            )
        ).mappings().all()

        assert [dict(row) for row in rows] == [
            {
                "table_name": "customer_return_disposition",
                "rls_enabled": True,
                "rls_forced": True,
                "app_select": True,
                "app_insert": True,
                "app_update": False,
                "app_delete": False,
            },
            {
                "table_name": "customer_return_quarantine_item",
                "rls_enabled": True,
                "rls_forced": True,
                "app_select": True,
                "app_insert": True,
                "app_update": False,
                "app_delete": False,
            },
        ]

        policies = (
            await connection.execute(
                text("""
                    SELECT tablename, policyname
                    FROM pg_catalog.pg_policies
                    WHERE schemaname = 'public'
                      AND tablename IN (
                        'customer_return_quarantine_item',
                        'customer_return_disposition'
                      )
                    ORDER BY tablename, policyname
                """)
            )
        ).all()
        assert [tuple(row) for row in policies] == [
            (
                "customer_return_disposition",
                "customer_return_disposition_schema_owner",
            ),
            (
                "customer_return_disposition",
                "customer_return_disposition_tenant_access",
            ),
            (
                "customer_return_quarantine_item",
                "customer_return_quarantine_item_schema_owner",
            ),
            (
                "customer_return_quarantine_item",
                "customer_return_quarantine_item_tenant_access",
            ),
        ]

        trigger_names = set(
            (
                await connection.execute(
                    text("""
                        SELECT triggers.tgname
                        FROM pg_catalog.pg_trigger AS triggers
                        JOIN pg_catalog.pg_class AS relations
                          ON relations.oid = triggers.tgrelid
                        WHERE NOT triggers.tgisinternal
                          AND relations.relname IN (
                            'customer_return_quarantine_item',
                            'customer_return_disposition',
                            'sale'
                          )
                          AND triggers.tgname LIKE '%customer_return%'
                    """)
                )
            ).scalars()
        )
        assert {
            "trg_immutable_customer_return_quarantine",
            "trg_immutable_customer_return_disposition",
            "trg_audit_customer_return_quarantine",
            "trg_audit_customer_return_disposition",
            "trg_validate_customer_return_quarantine_insert",
            "trg_validate_customer_return_disposition_insert",
            "trg_require_return_quarantine_before_completion",
        } <= trigger_names
