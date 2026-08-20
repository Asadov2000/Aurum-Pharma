"""Least-privilege and concurrency contract for the billing worker."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.time import utc_now


@pytest_asyncio.fixture
async def billing_worker_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        os.environ["DATABASE_URL_BILLING_WORKER"],
        poolclass=NullPool,
    )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def support_role_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_SUPPORT, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _insert_subscription(
    maintenance_engine: AsyncEngine,
    *,
    status: str,
    period_end_offset: timedelta,
) -> tuple[UUID, UUID]:
    tenant_id = uuid4()
    subscription_id = uuid4()
    async with maintenance_engine.begin() as connection:
        plan_id = await connection.scalar(
            text("SELECT id FROM public.subscription_plan WHERE code = 'aurum_pharma'")
        )
        assert plan_id is not None
        await connection.execute(
            text("""
                INSERT INTO public.tenant (
                  id, name, contact_email, status, trial_started_at, trial_ends_at
                ) VALUES (
                  :tenant_id, 'Billing worker boundary', :email, :tenant_status,
                  pg_catalog.statement_timestamp() - INTERVAL '30 days',
                  pg_catalog.statement_timestamp() - INTERVAL '1 day'
                )
                """),
            {
                "tenant_id": tenant_id,
                "email": f"billing-worker-{tenant_id}@example.invalid",
                "tenant_status": "trial" if status == "trial" else "grace_period",
            },
        )
        await connection.execute(
            text("""
                INSERT INTO public.tenant_subscription (
                  id, tenant_id, plan_id, status, billing_period,
                  period_start, period_end, branches_count, amount, currency
                ) VALUES (
                  :subscription_id, :tenant_id, :plan_id, :status, 'monthly',
                  :period_start, :period_end, 1, 100.00, 'TJS'
                )
                """),
            {
                "subscription_id": subscription_id,
                "tenant_id": tenant_id,
                "plan_id": plan_id,
                "status": status,
                "period_start": utc_now() - timedelta(days=30),
                "period_end": utc_now() + period_end_offset,
            },
        )
    return tenant_id, subscription_id


async def _cleanup_tenant(maintenance_engine: AsyncEngine, tenant_id: UUID) -> None:
    async with maintenance_engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM public.tenant_subscription WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        await connection.execute(
            text("DELETE FROM public.tenant WHERE id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        await connection.execute(
            text("DELETE FROM public.audit_log WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )


async def test_worker_has_only_exact_transition_commands(
    billing_worker_engine: AsyncEngine,
) -> None:
    async with billing_worker_engine.connect() as connection:
        identity = (
            (
                await connection.execute(
                    text("""
                        SELECT
                          session_user,
                          current_user,
                          has_database_privilege(current_database(), 'CREATE') AS can_create,
                          has_database_privilege(current_database(), 'TEMP') AS can_temp,
                          has_schema_privilege('public', 'CREATE') AS can_create_schema_object,
                          (SELECT rolconnlimit FROM pg_catalog.pg_roles
                           WHERE rolname = current_user) AS connection_limit,
                          NOT EXISTS (
                            SELECT 1 FROM pg_catalog.pg_auth_members AS membership
                            JOIN pg_catalog.pg_roles AS member ON member.oid = membership.member
                            WHERE member.rolname = current_user
                          ) AS has_no_memberships
                        """),
                )
            )
            .mappings()
            .one()
        )
        direct_privileges = (
            await connection.execute(
                text("""
                    SELECT relation.relname, privilege.name
                    FROM pg_catalog.pg_class AS relation
                    JOIN pg_catalog.pg_namespace AS schema
                      ON schema.oid = relation.relnamespace
                    CROSS JOIN (VALUES
                      ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE'),
                      ('TRUNCATE'), ('REFERENCES'), ('TRIGGER')
                    ) AS privilege(name)
                    WHERE schema.nspname = 'public'
                      AND relation.relkind IN ('r', 'p', 'v', 'm')
                      AND has_table_privilege(relation.oid, privilege.name)
                    ORDER BY relation.relname, privilege.name
                    """),
            )
        ).all()
        usable_schemas = (
            (
                await connection.execute(
                    text("""
                    SELECT schema.nspname
                    FROM pg_catalog.pg_namespace AS schema
                    WHERE schema.nspname <> 'information_schema'
                      AND schema.nspname !~ '^pg_'
                      AND has_schema_privilege(schema.oid, 'USAGE')
                    ORDER BY schema.nspname
                    """),
                )
            )
            .scalars()
            .all()
        )
        usable_sequences = (
            (
                await connection.execute(
                    text("""
                    SELECT relation.relname
                    FROM pg_catalog.pg_class AS relation
                    JOIN pg_catalog.pg_namespace AS schema
                      ON schema.oid = relation.relnamespace
                    WHERE schema.nspname = 'public'
                      AND CASE
                            WHEN relation.relkind = 'S'
                            THEN has_sequence_privilege(relation.oid, 'USAGE')
                            ELSE FALSE
                          END
                    ORDER BY relation.relname
                    """),
                )
            )
            .scalars()
            .all()
        )
        executable_functions = (
            (
                await connection.execute(
                    text("""
                    SELECT routine.proname
                    FROM pg_catalog.pg_proc AS routine
                    JOIN pg_catalog.pg_namespace AS schema
                      ON schema.oid = routine.pronamespace
                    WHERE schema.nspname = 'public'
                      AND has_function_privilege(routine.oid, 'EXECUTE')
                    ORDER BY routine.proname
                    """),
                )
            )
            .scalars()
            .all()
        )

    assert dict(identity) == {
        "session_user": "aurum_billing_worker",
        "current_user": "aurum_billing_worker",
        "can_create": False,
        "can_temp": False,
        "can_create_schema_object": False,
        "connection_limit": 2,
        "has_no_memberships": True,
    }
    assert direct_privileges == []
    assert usable_schemas == ["public"]
    assert usable_sequences == []
    assert executable_functions == [
        "process_billing_grace_endings",
        "process_billing_trial_endings",
    ]


async def test_support_cannot_execute_worker_commands(
    support_role_engine: AsyncEngine,
) -> None:
    async with support_role_engine.connect() as connection:
        privileges = (
            (
                await connection.execute(
                    text("""
                        SELECT
                          has_function_privilege(
                            'public.process_billing_trial_endings(integer)', 'EXECUTE'
                          ) AS trial,
                          has_function_privilege(
                            'public.process_billing_grace_endings(integer)', 'EXECUTE'
                          ) AS grace
                        """),
                )
            )
            .mappings()
            .one()
        )
    assert dict(privileges) == {"trial": False, "grace": False}


async def test_worker_commands_have_hardened_definer_metadata(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        rows = (
            (
                await connection.execute(
                    text("""
                    SELECT
                      routine.proname,
                      owner.rolname AS owner,
                      routine.prosecdef,
                      routine.proconfig
                    FROM pg_catalog.pg_proc AS routine
                    JOIN pg_catalog.pg_namespace AS schema
                      ON schema.oid = routine.pronamespace
                    JOIN pg_catalog.pg_roles AS owner
                      ON owner.oid = routine.proowner
                    WHERE schema.nspname = 'public'
                      AND routine.proname IN (
                        'process_billing_trial_endings',
                        'process_billing_grace_endings'
                      )
                    ORDER BY routine.proname
                    """),
                )
            )
            .mappings()
            .all()
        )

    assert [dict(row) for row in rows] == [
        {
            "proname": "process_billing_grace_endings",
            "owner": "aurum_schema_owner",
            "prosecdef": True,
            "proconfig": [
                "search_path=pg_catalog, pg_temp",
                "lock_timeout=5s",
                "statement_timeout=30s",
            ],
        },
        {
            "proname": "process_billing_trial_endings",
            "owner": "aurum_schema_owner",
            "prosecdef": True,
            "proconfig": [
                "search_path=pg_catalog, pg_temp",
                "lock_timeout=5s",
                "statement_timeout=30s",
            ],
        },
    ]


async def test_trial_and_grace_transitions_are_retry_safe(
    billing_worker_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    trial_tenant, trial_subscription = await _insert_subscription(
        maintenance_engine,
        status="trial",
        period_end_offset=timedelta(hours=-1),
    )
    grace_tenant, grace_subscription = await _insert_subscription(
        maintenance_engine,
        status="grace_period",
        period_end_offset=timedelta(days=-8),
    )
    try:
        async with billing_worker_engine.begin() as connection:
            assert (
                await connection.scalar(text("SELECT public.process_billing_trial_endings(100)"))
                == 1
            )
            assert (
                await connection.scalar(text("SELECT public.process_billing_grace_endings(100)"))
                == 1
            )
            assert (
                await connection.scalar(text("SELECT public.process_billing_grace_endings(100)"))
                == 0
            )

        async with maintenance_engine.connect() as connection:
            trial_status = await connection.scalar(
                text("SELECT status FROM public.tenant_subscription WHERE id = :id"),
                {"id": trial_subscription},
            )
            grace_state = (
                (
                    await connection.execute(
                        text("""
                            SELECT subscription.status, tenant.status AS tenant_status
                            FROM public.tenant_subscription AS subscription
                            JOIN public.tenant AS tenant ON tenant.id = subscription.tenant_id
                            WHERE subscription.id = :id
                            """),
                        {"id": grace_subscription},
                    )
                )
                .mappings()
                .one()
            )
        assert trial_status == "grace_period"
        assert dict(grace_state) == {"status": "suspended", "tenant_status": "readonly"}
    finally:
        await _cleanup_tenant(maintenance_engine, trial_tenant)
        await _cleanup_tenant(maintenance_engine, grace_tenant)


async def test_concurrent_workers_count_one_transition(
    billing_worker_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    tenant_id, _ = await _insert_subscription(
        maintenance_engine,
        status="trial",
        period_end_offset=timedelta(hours=-1),
    )

    async def run_once() -> int:
        async with billing_worker_engine.begin() as connection:
            moved = await connection.scalar(
                text("SELECT public.process_billing_trial_endings(100)")
            )
            return int(moved or 0)

    try:
        results = await asyncio.gather(run_once(), run_once())
        assert sum(results) == 1
    finally:
        await _cleanup_tenant(maintenance_engine, tenant_id)


async def test_batch_size_is_rejected_in_database(
    billing_worker_engine: AsyncEngine,
) -> None:
    with pytest.raises(DBAPIError) as error:
        async with billing_worker_engine.begin() as connection:
            await connection.execute(text("SELECT public.process_billing_trial_endings(101)"))
    assert getattr(error.value.orig, "sqlstate", None) == "22023"
