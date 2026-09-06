"""Least-privilege contract for the general system worker."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

WORKER_FUNCTIONS = [
    "worker_claim_notification_deliveries(integer,uuid)",
    "worker_complete_notification_delivery(uuid,uuid,text,text)",
    "worker_enqueue_expiring_license_notifications(integer)",
    "worker_list_automatic_trial_candidates(integer,uuid[])",
    "worker_purge_expired_email_codes(integer)",
    "worker_purge_expired_sessions(integer)",
    "worker_purge_old_notifications(integer)",
    "worker_start_automatic_trial(uuid)",
]


@pytest_asyncio.fixture
async def system_worker_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(os.environ["DATABASE_URL_WORKER"], poolclass=NullPool)
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


async def test_worker_has_only_exact_function_allowlist(
    system_worker_engine: AsyncEngine,
) -> None:
    async with system_worker_engine.connect() as connection:
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
                            SELECT 1
                            FROM pg_catalog.pg_parameter_acl AS parameters
                            CROSS JOIN LATERAL
                              pg_catalog.aclexplode(parameters.paracl) AS acl
                            JOIN pg_catalog.pg_roles AS grantee
                              ON grantee.oid = acl.grantee
                            WHERE grantee.rolname = current_user
                          ) AS has_no_parameter_privileges,
                          NOT EXISTS (
                            SELECT 1 FROM pg_catalog.pg_auth_members AS membership
                            JOIN pg_catalog.pg_roles AS member
                              ON member.oid = membership.member
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
        executable_functions = (
            (
                await connection.execute(
                    text("""
                        SELECT routine.oid::REGPROCEDURE::TEXT
                        FROM pg_catalog.pg_proc AS routine
                        JOIN pg_catalog.pg_namespace AS schema
                          ON schema.oid = routine.pronamespace
                        WHERE schema.nspname = 'public'
                          AND has_function_privilege(routine.oid, 'EXECUTE')
                        ORDER BY routine.oid::REGPROCEDURE::TEXT
                        """),
                )
            )
            .scalars()
            .all()
        )

    assert dict(identity) == {
        "session_user": "aurum_worker",
        "current_user": "aurum_worker",
        "can_create": False,
        "can_temp": False,
        "can_create_schema_object": False,
        "connection_limit": 2,
        "has_no_parameter_privileges": True,
        "has_no_memberships": True,
    }
    assert direct_privileges == []
    assert executable_functions == WORKER_FUNCTIONS


async def test_worker_cannot_read_tables_directly(
    system_worker_engine: AsyncEngine,
) -> None:
    with pytest.raises(DBAPIError) as error:
        async with system_worker_engine.begin() as connection:
            await connection.execute(text("SELECT id FROM public.tenant LIMIT 1"))
    assert getattr(error.value.orig, "sqlstate", None) == "42501"


async def test_support_cannot_execute_worker_commands(
    support_role_engine: AsyncEngine,
) -> None:
    async with support_role_engine.connect() as connection:
        privileges = (
            (
                await connection.execute(
                    text("""
                        SELECT routine.oid::REGPROCEDURE::TEXT
                        FROM pg_catalog.pg_proc AS routine
                        JOIN pg_catalog.pg_namespace AS schema
                          ON schema.oid = routine.pronamespace
                        WHERE schema.nspname = 'public'
                          AND routine.proname LIKE 'worker_%'
                          AND has_function_privilege(routine.oid, 'EXECUTE')
                        ORDER BY routine.oid::REGPROCEDURE::TEXT
                        """),
                )
            )
            .scalars()
            .all()
        )
    assert privileges == []


async def test_worker_commands_have_hardened_definer_metadata(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        rows = (
            (
                await connection.execute(
                    text("""
                        SELECT
                          routine.oid::REGPROCEDURE::TEXT AS signature,
                          owner.rolname AS owner,
                          routine.prosecdef,
                          routine.proconfig
                        FROM pg_catalog.pg_proc AS routine
                        JOIN pg_catalog.pg_namespace AS schema
                          ON schema.oid = routine.pronamespace
                        JOIN pg_catalog.pg_roles AS owner
                          ON owner.oid = routine.proowner
                        WHERE schema.nspname = 'public'
                          AND routine.proname LIKE 'worker_%'
                        ORDER BY routine.oid::REGPROCEDURE::TEXT
                        """),
                )
            )
            .mappings()
            .all()
        )

    assert [row["signature"] for row in rows] == WORKER_FUNCTIONS
    for row in rows:
        assert row["owner"] == "aurum_schema_owner"
        assert row["prosecdef"] is True
        assert row["proconfig"] == [
            "search_path=pg_catalog, pg_temp",
            "lock_timeout=5s",
            "statement_timeout=30s",
        ]


async def _insert_pending_delivery(
    maintenance_engine: AsyncEngine,
) -> tuple[UUID, UUID, UUID, UUID]:
    tenant_id = uuid4()
    user_id = uuid4()
    notification_id = uuid4()
    delivery_id = uuid4()
    async with maintenance_engine.begin() as connection:
        await connection.execute(
            text("""
                INSERT INTO public.tenant (id, name, contact_email)
                VALUES (:tenant_id, 'Worker delivery boundary', :email)
                """),
            {
                "tenant_id": tenant_id,
                "email": f"worker-delivery-{tenant_id}@example.invalid",
            },
        )
        await connection.execute(
            text("""
                INSERT INTO public.app_user (
                  id, email, full_name, home_tenant_id, status
                ) VALUES (
                  :user_id, :email, 'Worker delivery recipient', :tenant_id, 'active'
                )
                """),
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
                "email": f"worker-recipient-{user_id}@example.invalid",
            },
        )
        await connection.execute(
            text("""
                INSERT INTO public.notification (
                  id, tenant_id, user_id, event_type, title
                ) VALUES (
                  :notification_id, :tenant_id, :user_id,
                  'worker_boundary', 'Worker delivery boundary'
                )
                """),
            {
                "notification_id": notification_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
            },
        )
        await connection.execute(
            text("""
                INSERT INTO public.notification_delivery (
                  id, notification_id, channel, recipient, available_at, created_at
                ) VALUES (
                  :delivery_id, :notification_id, 'email', :recipient,
                  TIMESTAMPTZ '2000-01-01 00:00:00+00',
                  TIMESTAMPTZ '2000-01-01 00:00:00+00'
                )
                """),
            {
                "delivery_id": delivery_id,
                "notification_id": notification_id,
                "recipient": f"worker-recipient-{user_id}@example.invalid",
            },
        )
    return tenant_id, user_id, notification_id, delivery_id


async def _cleanup_pending_delivery(
    maintenance_engine: AsyncEngine,
    *,
    tenant_id: UUID,
    user_id: UUID,
    notification_id: UUID,
) -> None:
    async with maintenance_engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM public.notification WHERE id = :notification_id"),
            {"notification_id": notification_id},
        )
        await connection.execute(
            text("DELETE FROM public.app_user WHERE id = :user_id"),
            {"user_id": user_id},
        )
        await connection.execute(
            text("DELETE FROM public.tenant WHERE id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        await connection.execute(
            text("DELETE FROM public.audit_log WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )


async def test_delivery_requires_claim_and_retries_without_false_success(
    system_worker_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    tenant_id, user_id, notification_id, delivery_id = await _insert_pending_delivery(
        maintenance_engine
    )
    claim_token = uuid4()
    try:
        async with system_worker_engine.begin() as connection:
            claimed = (
                (
                    await connection.execute(
                        text("""
                            SELECT *
                            FROM public.worker_claim_notification_deliveries(
                              :limit, :claim_token
                            )
                            """),
                        {"limit": 1, "claim_token": claim_token},
                    )
                )
                .mappings()
                .one()
            )
        assert dict(claimed) == {
            "delivery_id": delivery_id,
            "notification_id": notification_id,
            "channel": "email",
            "attempt": 1,
        }

        with pytest.raises(DBAPIError) as stale_error:
            async with system_worker_engine.begin() as connection:
                await connection.execute(
                    text("""
                        SELECT public.worker_complete_notification_delivery(
                          :delivery_id, :claim_token, 'sent', NULL
                        )
                        """),
                    {"delivery_id": delivery_id, "claim_token": uuid4()},
                )
        assert getattr(stale_error.value.orig, "sqlstate", None) == "40001"

        async with system_worker_engine.begin() as connection:
            result = await connection.scalar(
                text("""
                    SELECT public.worker_complete_notification_delivery(
                      :delivery_id, :claim_token, 'retry',
                      'delivery_adapter_unavailable'
                    )
                    """),
                {"delivery_id": delivery_id, "claim_token": claim_token},
            )
        assert result == "pending"

        async with maintenance_engine.connect() as connection:
            state = (
                (
                    await connection.execute(
                        text("""
                            SELECT status, attempts, sent_at, claimed_at,
                                   claim_token, last_error_code,
                                   available_at > created_at AS retry_was_delayed
                            FROM public.notification_delivery
                            WHERE id = :delivery_id
                            """),
                        {"delivery_id": delivery_id},
                    )
                )
                .mappings()
                .one()
            )
        assert dict(state) == {
            "status": "pending",
            "attempts": 1,
            "sent_at": None,
            "claimed_at": None,
            "claim_token": None,
            "last_error_code": "delivery_adapter_unavailable",
            "retry_was_delayed": True,
        }
    finally:
        await _cleanup_pending_delivery(
            maintenance_engine,
            tenant_id=tenant_id,
            user_id=user_id,
            notification_id=notification_id,
        )


async def test_license_notification_is_deduplicated_for_active_owner(
    system_worker_engine: AsyncEngine,
    support_role_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    tenant_id = uuid4()
    user_id = uuid4()
    membership_id = uuid4()
    branch_ids = [UUID(int=1), UUID(int=2)]
    created_notification_ids: list[UUID] = []
    async with maintenance_engine.connect() as connection:
        existing_notification_ids = set(
            (
                await connection.execute(
                    text("""
                        SELECT id FROM public.notification
                        WHERE event_type = 'license_expiring'
                        """),
                )
            )
            .scalars()
            .all()
        )
    try:
        async with support_role_engine.begin() as connection:
            await connection.execute(text("SELECT set_config('app.support_session', 'true', true)"))
            await connection.execute(
                text("""
                    INSERT INTO public.tenant (id, name, contact_email, status)
                    VALUES (:tenant_id, 'Worker license boundary', :email, 'active')
                    """),
                {
                    "tenant_id": tenant_id,
                    "email": f"worker-license-{tenant_id}@example.invalid",
                },
            )
            await connection.execute(
                text("""
                    INSERT INTO public.app_user (
                      id, email, full_name, home_tenant_id, status
                    ) VALUES (
                      :user_id, :email, 'Worker license owner', :tenant_id, 'active'
                    )
                    """),
                {
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                    "email": f"worker-license-owner-{user_id}@example.invalid",
                },
            )
            await connection.execute(
                text("""
                    INSERT INTO public.tenant_membership (
                      id, tenant_id, user_id, full_name, status, activated_at
                    ) VALUES (
                      :membership_id, :tenant_id, :user_id,
                      'Worker license owner', 'active', statement_timestamp()
                    )
                    """),
                {
                    "membership_id": membership_id,
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                },
            )
            await connection.execute(
                text("""
                    INSERT INTO public.tenant_ownership (
                      tenant_id, membership_id, is_active
                    ) VALUES (:tenant_id, :membership_id, true)
                    """),
                {"tenant_id": tenant_id, "membership_id": membership_id},
            )
            await connection.execute(
                text("""
                    INSERT INTO public.branch (
                      id, tenant_id, name, license_number, license_expires_at
                    )
                    SELECT
                      branch_id, :tenant_id,
                      'Worker license branch ' || branch_id::TEXT,
                      'WORKER-' || branch_id::TEXT,
                      (statement_timestamp() AT TIME ZONE 'Asia/Dushanbe')::DATE
                    FROM unnest(CAST(:branch_ids AS UUID[])) AS branch_id
                    """),
                {
                    "branch_ids": branch_ids,
                    "tenant_id": tenant_id,
                },
            )

        async with system_worker_engine.begin() as connection:
            first_count = await connection.scalar(
                text("SELECT public.worker_enqueue_expiring_license_notifications(1)")
            )
        async with system_worker_engine.begin() as connection:
            second_count = await connection.scalar(
                text("SELECT public.worker_enqueue_expiring_license_notifications(1)")
            )

        async with maintenance_engine.connect() as connection:
            notifications = (
                (
                    await connection.execute(
                        text("""
                            SELECT id, dedupe_key
                            FROM public.notification
                            WHERE tenant_id = :tenant_id
                              AND user_id = :user_id
                              AND event_type = 'license_expiring'
                            """),
                        {"tenant_id": tenant_id, "user_id": user_id},
                    )
                )
                .mappings()
                .all()
            )
            current_notification_ids = set(
                (
                    await connection.execute(
                        text("""
                            SELECT id FROM public.notification
                            WHERE event_type = 'license_expiring'
                            """),
                    )
                )
                .scalars()
                .all()
            )
        created_notification_ids = list(current_notification_ids - existing_notification_ids)
        assert first_count == 1
        assert second_count == 1
        assert len(notifications) == 2
        assert {row["dedupe_key"].split(":", 1)[0] for row in notifications} == {
            str(branch_id) for branch_id in branch_ids
        }
    finally:
        async with maintenance_engine.begin() as connection:
            if created_notification_ids:
                await connection.execute(
                    text("DELETE FROM public.notification WHERE id = ANY(:ids)"),
                    {"ids": created_notification_ids},
                )
        async with support_role_engine.begin() as connection:
            await connection.execute(text("SELECT set_config('app.support_session', 'true', true)"))
            await connection.execute(
                text("DELETE FROM public.sync_stream WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
            await connection.execute(
                text("DELETE FROM public.sync_writer_epoch WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
            await connection.execute(
                text("DELETE FROM public.sync_node WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
            await connection.execute(
                text("DELETE FROM public.tenant WHERE id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
        async with maintenance_engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM public.app_user WHERE id = :user_id"),
                {"user_id": user_id},
            )
            await connection.execute(
                text("DELETE FROM public.audit_log WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
