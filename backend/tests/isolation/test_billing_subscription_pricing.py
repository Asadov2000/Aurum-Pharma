"""Database boundaries for immutable tenant subscription pricing snapshots."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine


async def test_subscription_price_application_is_closed_and_tenant_read_only(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        relation = (await connection.execute(text("""
                    SELECT
                      pg_get_userbyid(relowner) AS owner,
                      relrowsecurity,
                      relforcerowsecurity
                    FROM pg_catalog.pg_class
                    WHERE oid = 'public.billing_subscription_price_application'::regclass
                    """))).mappings().one()
        privileges = list((await connection.execute(text("""
                        SELECT role_name, privilege,
                          has_table_privilege(
                            role_name,
                            'public.billing_subscription_price_application',
                            privilege
                          ) AS allowed
                        FROM (VALUES ('aurum_app'), ('aurum_support')) AS roles(role_name)
                        CROSS JOIN (VALUES ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE'))
                          AS checks(privilege)
                        ORDER BY role_name, privilege
                        """))).mappings())
        command = (await connection.execute(text("""
                    SELECT
                      pg_get_userbyid(proowner) AS owner,
                      prosecdef,
                      array_to_string(proconfig, ',') AS config,
                      has_function_privilege(
                        'aurum_support', oid, 'EXECUTE'
                      ) AS support_execute,
                      has_function_privilege('aurum_app', oid, 'EXECUTE') AS app_execute,
                      has_function_privilege('public', oid, 'EXECUTE') AS public_execute
                    FROM pg_catalog.pg_proc
                    WHERE oid = (
                      'public.apply_initial_subscription_price('
                      'uuid,uuid,uuid,text,uuid,uuid,integer)'
                    )::regprocedure
                    """))).mappings().one()

    assert relation == {
        "owner": "aurum_schema_owner",
        "relrowsecurity": True,
        "relforcerowsecurity": True,
    }
    assert {(row["role_name"], row["privilege"]) for row in privileges if row["allowed"]} == {
        ("aurum_app", "SELECT")
    }
    assert command == {
        "owner": "aurum_schema_owner",
        "prosecdef": True,
        "config": "search_path=pg_catalog, pg_temp",
        "support_execute": True,
        "app_execute": False,
        "public_execute": False,
    }


@pytest.mark.parametrize(
    ("command_name", "arguments"),
    (
        (
            "create_billing_price_draft",
            "uuid,uuid,uuid,text,uuid,numeric,numeric,text,smallint,text,jsonb",
        ),
        (
            "approve_and_schedule_billing_price",
            "uuid,uuid,uuid,text,uuid,integer,timestamp with time zone",
        ),
        (
            "activate_billing_price_version",
            "uuid,uuid,uuid,text,uuid,integer",
        ),
        (
            "cancel_scheduled_billing_price",
            "uuid,uuid,uuid,text,uuid,integer,text,text",
        ),
    ),
)
async def test_pricing_commands_lock_operation_then_plan_before_mutation(
    maintenance_engine: AsyncEngine,
    command_name: str,
    arguments: str,
) -> None:
    signature = f"public.{command_name}({arguments})"
    unlocked_signature = f"public.{command_name}_unlocked_0095({arguments})"
    async with maintenance_engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text("""
                    SELECT
                      pg_get_functiondef(CAST(:signature AS regprocedure)) AS definition,
                      has_function_privilege(
                        'aurum_support', CAST(:signature AS regprocedure), 'EXECUTE'
                      ) AS support_execute,
                      has_function_privilege(
                        'aurum_support', CAST(:unlocked AS regprocedure), 'EXECUTE'
                      ) AS support_execute_unlocked
                    """),
                    {"signature": signature, "unlocked": unlocked_signature},
                )
            )
            .mappings()
            .one()
        )

    definition = str(row["definition"])
    assert definition.index("9501") < definition.index("9602")
    assert definition.index("9602") < definition.rindex("_unlocked_0095")
    assert row["support_execute"] is True
    assert row["support_execute_unlocked"] is False


async def test_branch_scope_lock_covers_both_tenants_on_transfer(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        definition = str(await connection.scalar(text("""
                SELECT pg_get_functiondef(
                  'public.trg_lock_tenant_billing_scope()'::regprocedure
                )
                """)))

    assert "OLD.tenant_id IS DISTINCT FROM NEW.tenant_id" in definition
    assert "OLD.tenant_id::TEXT < NEW.tenant_id::TEXT" in definition
    assert definition.count("hashtextextended") == 2


@pytest.mark.parametrize(
    ("period_start", "billing_period", "anchor_day", "expected"),
    (
        (
            datetime(2027, 1, 30, 19, 15, tzinfo=UTC),
            "monthly",
            31,
            datetime(2027, 2, 27, 19, 15, tzinfo=UTC),
        ),
        (
            datetime(2028, 1, 30, 19, 15, tzinfo=UTC),
            "monthly",
            31,
            datetime(2028, 2, 28, 19, 15, tzinfo=UTC),
        ),
        (
            datetime(2028, 2, 28, 19, 15, tzinfo=UTC),
            "yearly",
            29,
            datetime(2029, 2, 27, 19, 15, tzinfo=UTC),
        ),
    ),
)
async def test_billing_calendar_clamps_month_end_in_dushanbe(
    maintenance_engine: AsyncEngine,
    period_start: datetime,
    billing_period: str,
    anchor_day: int,
    expected: datetime,
) -> None:
    async with maintenance_engine.connect() as connection:
        actual = await connection.scalar(
            text(
                "SELECT public.calculate_billing_period_end("
                ":period_start, :billing_period, 'Asia/Dushanbe', :anchor_day)"
            ),
            {
                "period_start": period_start,
                "billing_period": billing_period,
                "anchor_day": anchor_day,
            },
        )
    assert actual == expected


async def test_subscription_price_application_rejects_update_and_delete(
    maintenance_engine: AsyncEngine,
) -> None:
    suffix = uuid4().hex
    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            fixture = (
                (
                    await connection.execute(
                        text("""
                        WITH actor AS (
                          INSERT INTO public.app_user (email, full_name, status)
                          VALUES (:email, 'Pricing snapshot actor', 'active')
                          RETURNING id
                        ), target_tenant AS (
                          INSERT INTO public.tenant (name, contact_email)
                          VALUES (:tenant_name, :tenant_email)
                          RETURNING id
                        ), versioned_plan AS (
                          INSERT INTO public.billing_plan (code, name, created_by)
                          SELECT :code, 'Snapshot plan', actor.id FROM actor
                          RETURNING id
                        ), draft_price AS (
                          INSERT INTO public.billing_price_version (
                            plan_id, version_number, monthly_price_per_branch, created_by
                          )
                          SELECT versioned_plan.id, 1, 590.00, actor.id
                          FROM versioned_plan, actor
                          RETURNING id, plan_id
                        ), tenant_subscription_row AS (
                          INSERT INTO public.tenant_subscription (
                            tenant_id, plan_id, status, period_end, branches_count, amount
                          )
                          SELECT
                            target_tenant.id,
                            legacy_plan.id,
                            'trial',
                            statement_timestamp() + interval '30 days',
                            1,
                            550.00
                          FROM target_tenant
                          CROSS JOIN public.subscription_plan AS legacy_plan
                          WHERE legacy_plan.code = 'aurum_pharma'
                          RETURNING id, tenant_id
                        )
                        SELECT
                          actor.id AS actor_id,
                          target_tenant.id AS tenant_id,
                          versioned_plan.id AS plan_id,
                          draft_price.id AS price_id,
                          tenant_subscription_row.id AS subscription_id
                        FROM actor, target_tenant, versioned_plan, draft_price,
                          tenant_subscription_row
                        """),
                        {
                            "email": f"snapshot-{suffix}@example.invalid",
                            "tenant_name": f"Snapshot {suffix}",
                            "tenant_email": f"snapshot-{suffix}@aurum.tj",
                            "code": f"snapshot_{suffix}",
                        },
                    )
                )
                .mappings()
                .one()
            )
            application_id = (
                await connection.execute(
                    text("""
                        INSERT INTO public.billing_subscription_price_application (
                          tenant_id, subscription_id, plan_id, application_kind,
                          source_type, price_version_id, plan_code, plan_name,
                          billing_period, period_start, period_end, calendar_anchor_day,
                          timezone, branches_count, monthly_price_per_branch,
                          annual_discount_pct, calculated_amount, currency,
                          terms_snapshot, operation_id, request_hash, request_payload,
                          actor_user_id, actor_session_id, mfa_verified_at,
                          result_snapshot
                        ) VALUES (
                          :tenant_id, :subscription_id, :plan_id, 'initial',
                          'price_version', :price_id, :code, 'Snapshot plan',
                          'monthly', statement_timestamp(),
                          statement_timestamp() + interval '1 month', 1,
                          'Asia/Dushanbe', 1, 590.00, 20.00, 590.00, 'TJS',
                          '{}'::jsonb, :operation_id, :request_hash,
                          '{}'::jsonb, :actor_id, :actor_session_id,
                          statement_timestamp(), '{}'::jsonb
                        ) RETURNING id
                        """),
                    {
                        **fixture,
                        "code": f"snapshot_{suffix}",
                        "operation_id": uuid4(),
                        "request_hash": "a" * 64,
                        "actor_session_id": uuid4(),
                    },
                )
            ).scalar_one()

            for statement in (
                "UPDATE public.billing_subscription_price_application "
                "SET calculated_amount = 1 WHERE id = :application_id",
                "DELETE FROM public.billing_subscription_price_application "
                "WHERE id = :application_id",
            ):
                savepoint = await connection.begin_nested()
                try:
                    with pytest.raises(DBAPIError) as mutation_error:
                        await connection.execute(
                            text(statement),
                            {"application_id": application_id},
                        )
                    assert getattr(mutation_error.value.orig, "sqlstate", None) == "55000"
                finally:
                    await savepoint.rollback()
        finally:
            await transaction.rollback()
