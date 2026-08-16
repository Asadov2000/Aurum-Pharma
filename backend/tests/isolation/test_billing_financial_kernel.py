"""Database trust boundaries for the immutable billing financial kernel."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

TENANT_READ_TABLES = {
    "billing_invoice",
    "billing_invoice_line",
    "billing_payment",
    "billing_payment_allocation",
    "billing_tenant_credit",
}

PRIVATE_TABLES = {
    "billing_financial_operation",
    "billing_payment_review",
    "billing_payment_adjustment_request",
    "billing_payment_adjustment",
    "billing_payment_adjustment_allocation",
    "billing_journal_entry",
    "billing_journal_posting",
    "billing_outbox_event",
}

IMMUTABLE_TABLES = {
    "billing_financial_operation",
    "billing_invoice",
    "billing_invoice_line",
    "billing_payment",
    "billing_payment_allocation",
    "billing_payment_adjustment",
    "billing_payment_adjustment_allocation",
    "billing_tenant_credit",
    "billing_journal_entry",
    "billing_journal_posting",
}

PROTECTED_COMMANDS = {
    "approve_billing_payment_adjustment(uuid,uuid,uuid,text,uuid,uuid,integer)",
    "approve_billing_bank_payment(uuid,uuid,uuid,text,uuid,uuid,integer)",
    (
        "create_billing_payment_adjustment_request(uuid,uuid,uuid,text,uuid,uuid,text,"
        "numeric,text,text,timestamp with time zone,text)"
    ),
    (
        "create_billing_bank_payment_review(uuid,uuid,uuid,text,uuid,uuid,numeric,"
        "timestamp with time zone,text,text)"
    ),
    "issue_billing_subscription_invoice(uuid,uuid,uuid,text,uuid,uuid,integer)",
    "list_platform_billing_payment_adjustments(uuid,uuid,uuid,integer,integer)",
    "read_platform_billing_financial_account(uuid,uuid,uuid)",
    "reject_billing_bank_payment_review(uuid,uuid,uuid,text,uuid,uuid,integer,text,text)",
    "reject_billing_payment_adjustment(uuid,uuid,uuid,text,uuid,uuid,integer,text,text)",
}


async def test_financial_tables_force_rls_and_deny_runtime_mutation(
    maintenance_engine: AsyncEngine,
) -> None:
    tables = TENANT_READ_TABLES | PRIVATE_TABLES
    async with maintenance_engine.connect() as connection:
        relations = list(
            (
                await connection.execute(
                    text("""
                    SELECT
                      relation.relname,
                      pg_get_userbyid(relation.relowner) AS owner,
                      relation.relrowsecurity,
                      relation.relforcerowsecurity
                    FROM pg_catalog.pg_class AS relation
                    JOIN pg_catalog.pg_namespace AS namespace
                      ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = 'public'
                      AND relation.relname = ANY(CAST(:tables AS TEXT[]))
                    ORDER BY relation.relname
                    """),
                    {"tables": sorted(tables)},
                )
            )
            .mappings()
            .all()
        )
        privileges = list(
            (
                await connection.execute(
                    text("""
                    SELECT role_name, table_name, privilege,
                      has_table_privilege(
                        role_name,
                        'public.' || quote_ident(table_name),
                        privilege
                      ) AS allowed
                    FROM unnest(CAST(:roles AS TEXT[])) AS roles(role_name)
                    CROSS JOIN unnest(CAST(:tables AS TEXT[])) AS tables(table_name)
                    CROSS JOIN unnest(CAST(:privileges AS TEXT[]))
                      AS checks(privilege)
                    ORDER BY role_name, table_name, privilege
                    """),
                    {
                        "roles": ["aurum_app", "aurum_support"],
                        "tables": sorted(tables),
                        "privileges": ["SELECT", "INSERT", "UPDATE", "DELETE"],
                    },
                )
            )
            .mappings()
            .all()
        )
        policies = list(
            (
                await connection.execute(
                    text("""
                    SELECT tablename, policyname, roles, cmd, qual, with_check
                    FROM pg_catalog.pg_policies
                    WHERE schemaname = 'public'
                      AND tablename = ANY(CAST(:tables AS TEXT[]))
                    ORDER BY tablename, policyname
                    """),
                    {"tables": sorted(tables)},
                )
            )
            .mappings()
            .all()
        )

    assert {row["relname"] for row in relations} == tables
    assert all(row["owner"] == "aurum_schema_owner" for row in relations)
    assert all(row["relrowsecurity"] is True for row in relations)
    assert all(row["relforcerowsecurity"] is True for row in relations)

    allowed = {
        (row["role_name"], row["table_name"], row["privilege"])
        for row in privileges
        if row["allowed"]
    }
    assert allowed == {("aurum_app", table, "SELECT") for table in TENANT_READ_TABLES}
    for row in policies:
        combined_expression = f"{row['qual'] or ''} {row['with_check'] or ''}"
        assert "is_support_session" not in combined_expression
    tenant_policies = {
        row["tablename"]
        for row in policies
        if row["policyname"].endswith("_tenant_read")
        and row["roles"] == ["aurum_app"]
        and row["cmd"] == "SELECT"
        and "current_tenant_id" in str(row["qual"])
    }
    assert tenant_policies == TENANT_READ_TABLES


async def test_financial_commands_are_narrow_security_definers(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        commands = list(
            (
                await connection.execute(
                    text("""
                    SELECT
                      routine.oid::regprocedure::TEXT AS signature,
                      pg_get_userbyid(routine.proowner) AS owner,
                      routine.prosecdef,
                      array_to_string(routine.proconfig, ',') AS config,
                      has_function_privilege(
                        'aurum_support', routine.oid, 'EXECUTE'
                      ) AS support_execute,
                      has_function_privilege(
                        'aurum_app', routine.oid, 'EXECUTE'
                      ) AS app_execute,
                      has_function_privilege(
                        'public', routine.oid, 'EXECUTE'
                      ) AS public_execute
                    FROM pg_catalog.pg_proc AS routine
                    JOIN pg_catalog.pg_namespace AS namespace
                      ON namespace.oid = routine.pronamespace
                    WHERE namespace.nspname = 'public'
                      AND routine.oid::regprocedure::TEXT = ANY(
                        CAST(:commands AS TEXT[])
                      )
                    ORDER BY signature
                    """),
                    {"commands": sorted(PROTECTED_COMMANDS)},
                )
            )
            .mappings()
            .all()
        )
        immutable_triggers = set(
            await connection.scalars(
                text("""
                SELECT event_object_table
                FROM information_schema.triggers
                WHERE trigger_schema = 'public'
                  AND event_object_table = ANY(CAST(:tables AS TEXT[]))
                  AND trigger_name = 'trg_immutable_' || event_object_table
                """),
                {"tables": sorted(IMMUTABLE_TABLES)},
            )
        )
        sequence_privileges = (await connection.execute(text("""
                SELECT
                  has_sequence_privilege(
                    'aurum_app', 'public.billing_invoice_number_seq', 'USAGE'
                  ) AS app_usage,
                  has_sequence_privilege(
                    'aurum_support', 'public.billing_invoice_number_seq', 'USAGE'
                  ) AS support_usage,
                  has_sequence_privilege(
                    'public', 'public.billing_invoice_number_seq', 'USAGE'
                  ) AS public_usage
                """))).mappings().one()

    assert {row["signature"] for row in commands} == PROTECTED_COMMANDS
    assert all(row["owner"] == "aurum_schema_owner" for row in commands)
    assert all(row["prosecdef"] is True for row in commands)
    assert all(row["config"] == "search_path=pg_catalog, pg_temp" for row in commands)
    assert all(row["support_execute"] is True for row in commands)
    assert all(row["app_execute"] is False for row in commands)
    assert all(row["public_execute"] is False for row in commands)
    assert immutable_triggers == IMMUTABLE_TABLES
    assert sequence_privileges == {
        "app_usage": False,
        "support_usage": False,
        "public_usage": False,
    }


async def test_tenant_financial_projection_rechecks_identity_scope_and_permission(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        function = (
            (
                await connection.execute(
                    text("""
                SELECT
                  pg_get_userbyid(routine.proowner) AS owner,
                  routine.prosecdef,
                  array_to_string(routine.proconfig, ',') AS config,
                  routine.prosrc,
                  has_function_privilege(
                    'aurum_app', routine.oid, 'EXECUTE'
                  ) AS app_execute,
                  has_function_privilege(
                    'aurum_support', routine.oid, 'EXECUTE'
                  ) AS support_execute,
                  has_function_privilege(
                    'public', routine.oid, 'EXECUTE'
                  ) AS public_execute,
                  has_function_privilege(
                    'aurum_mailer', routine.oid, 'EXECUTE'
                  ) AS mailer_execute,
                  has_function_privilege(
                    'aurum_edge_cash_executor', routine.oid, 'EXECUTE'
                  ) AS edge_executor_execute,
                  has_function_privilege(
                    'aurum_edge_cash_owner', routine.oid, 'EXECUTE'
                  ) AS edge_owner_execute
                FROM pg_catalog.pg_proc AS routine
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = routine.pronamespace
                WHERE namespace.nspname = 'public'
                  AND routine.oid::regprocedure::TEXT =
                    'read_tenant_billing_financial_account(uuid,uuid)'
                """),
                )
            )
            .mappings()
            .one()
        )

    assert function["owner"] == "aurum_schema_owner"
    assert function["prosecdef"] is True
    assert function["config"] == "search_path=pg_catalog, pg_temp"
    assert function["app_execute"] is True
    assert function["support_execute"] is False
    assert function["public_execute"] is False
    assert function["mailer_execute"] is False
    assert function["edge_executor_execute"] is False
    assert function["edge_owner_execute"] is False
    source = str(function["prosrc"])
    assert "SESSION_USER <> 'aurum_app'" in source
    assert "current_setting('app.support_session', true)" in source
    assert "current_app_user_id()" in source
    assert "current_tenant_id()" in source
    assert "p_actor_user_id IS DISTINCT FROM public.current_app_user_id()" in source
    assert "tenant_actor_has_permission(p_tenant_id, 'billing.overview.view')" in source
    assert "tenant_actor_has_permission(p_tenant_id, 'billing.invoice.view')" in source
