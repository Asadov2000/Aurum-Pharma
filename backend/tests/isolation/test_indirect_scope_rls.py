"""RLS for tables whose security scope comes from a parent record."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.security import hash_code, hash_token
from app.domains.notifications.repository import NotificationsRepository
from app.domains.notifications.service import NotificationsService


@pytest_asyncio.fixture
async def support_engine_iso() -> AsyncIterator[AsyncEngine]:
    settings = get_settings()
    engine = create_async_engine(
        settings.DATABASE_URL_SUPPORT,
        poolclass=NullPool,
        connect_args={"server_settings": {"app.support_session": "true"}},
    )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def app_engine_iso() -> AsyncIterator[AsyncEngine]:
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL_APP, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _set_app_context(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
) -> None:
    await conn.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": tenant_id},
    )
    await conn.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": user_id},
    )


def _sqlstate(exc: DBAPIError) -> str | None:
    return getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)


async def _assert_rls_denied(
    engine: AsyncEngine,
    *,
    tenant_id: str,
    user_id: str,
    statement: str,
    params: Mapping[str, Any],
    expected_sqlstate: str = "42501",
) -> None:
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            await _set_app_context(conn, tenant_id=tenant_id, user_id=user_id)
            with pytest.raises(DBAPIError) as raised:
                await conn.execute(text(statement), dict(params))
            assert _sqlstate(raised.value) == expected_sqlstate
        finally:
            if transaction.is_active:
                await transaction.rollback()


async def test_indirect_scope_policy_contract(
    support_engine_iso: AsyncEngine,
) -> None:
    async with support_engine_iso.begin() as conn:
        rls_rows = (
            await conn.execute(
                text(
                    "SELECT relname, relrowsecurity "
                    "FROM pg_class "
                    "WHERE oid = ANY(ARRAY["
                    "'public.role'::regclass, "
                    "'public.role_permission'::regclass, "
                    "'public.notification_subscription'::regclass, "
                    "'public.notification_delivery'::regclass"
                    "])"
                )
            )
        ).all()
        policy_rows = (
            await conn.execute(
                text(
                    "SELECT tablename, policyname, permissive, cmd, "
                    "qual, with_check "
                    "FROM pg_policies "
                    "WHERE schemaname = 'public' "
                    "AND tablename = ANY(ARRAY["
                    "'role', "
                    "'role_permission', "
                    "'notification_subscription', "
                    "'notification_delivery'"
                    "])"
                )
            )
        ).all()
        delivery_constraint = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM pg_constraint "
                    "WHERE conrelid = 'public.notification_delivery'::regclass "
                    "AND conname = 'uq_notification_delivery_notification_channel' "
                    "AND contype = 'u'"
                )
            )
        ).scalar_one()

    assert {row.relname for row in rls_rows if row.relrowsecurity} == {
        "role",
        "role_permission",
        "notification_subscription",
        "notification_delivery",
    }
    assert {(row.tablename, row.policyname, row.permissive, row.cmd) for row in policy_rows} == {
        ("role", "role_read", "PERMISSIVE", "SELECT"),
        ("role", "role_write", "PERMISSIVE", "ALL"),
        ("role_permission", "role_permission_read", "PERMISSIVE", "SELECT"),
        ("role_permission", "role_permission_write", "PERMISSIVE", "ALL"),
        ("notification_subscription", "user_isolation", "PERMISSIVE", "ALL"),
    }
    assert all(
        "is_support_session" not in f"{row.qual or ''} {row.with_check or ''}"
        for row in policy_rows
    )
    assert delivery_constraint == 1


@dataclass(frozen=True)
class IndirectScopeRows:
    token: str
    tenant_ids: tuple[str, ...]
    user_ids: tuple[str, ...]
    role_ids: tuple[str, ...]
    notification_ids: tuple[str, ...]
    delivery_ids: tuple[str, ...]
    permission_codes: tuple[str, ...]


@pytest_asyncio.fixture
async def indirect_scope_rows(
    support_engine_iso: AsyncEngine,
) -> AsyncIterator[IndirectScopeRows]:
    token = uuid4().hex[:12]
    tenant_ids: list[str] = []
    user_ids: list[str] = []
    role_ids: list[str] = []
    notification_ids: list[str] = []
    delivery_ids: list[str] = []
    permission_codes: list[str] = []

    try:
        async with support_engine_iso.begin() as conn:
            for label in ("a", "b"):
                tenant_id = (
                    await conn.execute(
                        text(
                            "INSERT INTO tenant (name, contact_email) "
                            "VALUES (:name, :email) RETURNING id"
                        ),
                        {
                            "name": f"RLS indirect {label}-{token}",
                            "email": f"rls-{label}-{token}@example.invalid",
                        },
                    )
                ).scalar_one()
                tenant_ids.append(str(tenant_id))

            for index, label in enumerate(("a", "b")):
                user_id = (
                    await conn.execute(
                        text(
                            "INSERT INTO app_user "
                            "(email, full_name, home_tenant_id) "
                            "VALUES (:email, :full_name, :tenant_id) RETURNING id"
                        ),
                        {
                            "email": f"rls-user-{label}-{token}@example.invalid",
                            "full_name": f"RLS user {label}",
                            "tenant_id": tenant_ids[index],
                        },
                    )
                ).scalar_one()
                user_ids.append(str(user_id))

                role_id = (
                    await conn.execute(
                        text(
                            "INSERT INTO role "
                            "(tenant_id, name, level, is_system) "
                            "VALUES (:tenant_id, :name, 4, false) RETURNING id"
                        ),
                        {
                            "tenant_id": tenant_ids[index],
                            "name": f"RLS role {label}-{token}",
                        },
                    )
                ).scalar_one()
                role_ids.append(str(role_id))

                notification_id = (
                    await conn.execute(
                        text(
                            "INSERT INTO notification "
                            "(tenant_id, user_id, event_type, title) "
                            "VALUES (:tenant_id, :user_id, :event_type, :title) "
                            "RETURNING id"
                        ),
                        {
                            "tenant_id": tenant_ids[index],
                            "user_id": user_ids[index],
                            "event_type": f"security.rls.{label}",
                            "title": f"RLS event {label}",
                        },
                    )
                ).scalar_one()
                notification_ids.append(str(notification_id))

            writable_role_id = (
                await conn.execute(
                    text(
                        "INSERT INTO role "
                        "(tenant_id, name, level, is_system) "
                        "VALUES (:tenant_id, :name, 4, false) RETURNING id"
                    ),
                    {
                        "tenant_id": tenant_ids[0],
                        "name": f"RLS writable role-{token}",
                    },
                )
            ).scalar_one()
            role_ids.append(str(writable_role_id))

            recipient_user_id = (
                await conn.execute(
                    text(
                        "INSERT INTO app_user "
                        "(email, full_name, home_tenant_id) "
                        "VALUES (:email, :full_name, :tenant_id) RETURNING id"
                    ),
                    {
                        "email": f"rls-recipient-{token}@example.invalid",
                        "full_name": "RLS recipient",
                        "tenant_id": tenant_ids[0],
                    },
                )
            ).scalar_one()
            user_ids.append(str(recipient_user_id))
            membership_only_user_id = (
                await conn.execute(
                    text(
                        "INSERT INTO app_user "
                        "(email, full_name, home_tenant_id) "
                        "VALUES (:email, :full_name, :tenant_id) RETURNING id"
                    ),
                    {
                        "email": f"rls-member-{token}@example.invalid",
                        "full_name": "RLS membership only",
                        "tenant_id": tenant_ids[0],
                    },
                )
            ).scalar_one()
            user_ids.append(str(membership_only_user_id))
            recipient_notification_id = (
                await conn.execute(
                    text(
                        "INSERT INTO notification "
                        "(tenant_id, user_id, event_type, title) "
                        "VALUES (:tenant_id, :user_id, :event_type, :title) "
                        "RETURNING id"
                    ),
                    {
                        "tenant_id": tenant_ids[0],
                        "user_id": user_ids[2],
                        "event_type": "security.rls.recipient",
                        "title": "RLS recipient event",
                    },
                )
            ).scalar_one()
            notification_ids.append(str(recipient_notification_id))

            permission_codes = [
                str(row[0])
                for row in (
                    await conn.execute(
                        text(
                            "SELECT code FROM permission "
                            "WHERE is_active = true ORDER BY code LIMIT 2"
                        )
                    )
                ).all()
            ]
            assert len(permission_codes) == 2

            membership_scopes = ((0, 0), (1, 1), (2, 0), (3, 0))
            for user_index, tenant_index in membership_scopes:
                await conn.execute(
                    text(
                        "INSERT INTO tenant_membership "
                        "(tenant_id, user_id, full_name, status) "
                        "SELECT :tenant_id, id, full_name, 'active' "
                        "FROM app_user WHERE id = :user_id"
                    ),
                    {
                        "tenant_id": tenant_ids[tenant_index],
                        "user_id": user_ids[user_index],
                    },
                )

            for owner_index, tenant_index in ((0, 0), (1, 1)):
                await conn.execute(
                    text(
                        "INSERT INTO tenant_ownership (tenant_id, membership_id) "
                        "SELECT :tenant_id, id FROM tenant_membership "
                        "WHERE tenant_id = :tenant_id AND user_id = :user_id"
                    ),
                    {
                        "tenant_id": tenant_ids[tenant_index],
                        "user_id": user_ids[owner_index],
                    },
                )

            for user_index, tenant_index in ((0, 0), (1, 1), (2, 0)):
                role_index = 2 if user_index == 2 else tenant_index
                await conn.execute(
                    text(
                        "INSERT INTO user_assignment "
                        "(user_id, tenant_id, role_id, password_required) "
                        "VALUES (:user_id, :tenant_id, :role_id, :password_required)"
                    ),
                    {
                        "user_id": user_ids[user_index],
                        "tenant_id": tenant_ids[tenant_index],
                        "role_id": role_ids[role_index],
                        "password_required": user_index == 0,
                    },
                )

            for index in range(2):
                await conn.execute(
                    text(
                        "INSERT INTO role_permission (role_id, permission_code) "
                        "VALUES (:role_id, :permission_code)"
                    ),
                    {
                        "role_id": role_ids[index],
                        "permission_code": permission_codes[0],
                    },
                )
                await conn.execute(
                    text(
                        "INSERT INTO notification_subscription "
                        "(user_id, event_type) VALUES (:user_id, :event_type)"
                    ),
                    {
                        "user_id": user_ids[index],
                        "event_type": "security.rls.seed",
                    },
                )
                delivery_id = (
                    await conn.execute(
                        text(
                            "INSERT INTO notification_delivery "
                            "(notification_id, channel, recipient) "
                            "VALUES (:notification_id, 'email', :recipient) "
                            "RETURNING id"
                        ),
                        {
                            "notification_id": notification_ids[index],
                            "recipient": f"delivery-{index}-{token}@example.invalid",
                        },
                    )
                ).scalar_one()
                delivery_ids.append(str(delivery_id))

            await conn.execute(
                text(
                    "INSERT INTO role_permission (role_id, permission_code) "
                    "SELECT :role_id, permissions.code "
                    "FROM permission AS permissions "
                    "WHERE permissions.code = ANY(CAST(:codes AS TEXT[])) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "role_id": role_ids[0],
                    "codes": [
                        permission_codes[1],
                        "users.invite",
                        "users.block",
                        "roles.assign",
                        "roles.update",
                    ],
                },
            )

            await conn.execute(
                text(
                    "INSERT INTO notification_subscription "
                    "(user_id, event_type, channels) "
                    'VALUES (:user_id, :event_type, \'["in_app", "email"]\'::jsonb)'
                ),
                {
                    "user_id": user_ids[2],
                    "event_type": "security.rls.recipient",
                },
            )

        yield IndirectScopeRows(
            token=token,
            tenant_ids=tuple(tenant_ids),
            user_ids=tuple(user_ids),
            role_ids=tuple(role_ids),
            notification_ids=tuple(notification_ids),
            delivery_ids=tuple(delivery_ids),
            permission_codes=tuple(permission_codes),
        )
    finally:
        if tenant_ids or user_ids:
            async with support_engine_iso.begin() as conn:
                if tenant_ids:
                    await conn.execute(
                        text("DELETE FROM tenant WHERE id = ANY(CAST(:ids AS UUID[]))"),
                        {"ids": tenant_ids},
                    )
                    await conn.execute(
                        text(
                            "DELETE FROM audit_log " "WHERE tenant_id = ANY(CAST(:ids AS UUID[]))"
                        ),
                        {"ids": tenant_ids},
                    )
                if user_ids:
                    await conn.execute(
                        text("DELETE FROM app_user WHERE id = ANY(CAST(:ids AS UUID[]))"),
                        {"ids": user_ids},
                    )


async def test_indirect_scope_reads_are_isolated(
    support_engine_iso: AsyncEngine,
    app_engine_iso: AsyncEngine,
    indirect_scope_rows: IndirectScopeRows,
) -> None:
    rows = indirect_scope_rows
    for index in range(2):
        async with app_engine_iso.begin() as conn:
            await _set_app_context(
                conn,
                tenant_id=rows.tenant_ids[index],
                user_id=rows.user_ids[index],
            )
            visible_roles = {
                str(row[0])
                for row in (
                    await conn.execute(
                        text(
                            "SELECT role_id FROM role_permission "
                            "WHERE role_id = ANY(CAST(:ids AS UUID[]))"
                        ),
                        {"ids": rows.role_ids},
                    )
                ).all()
            }
            visible_subscriptions = {
                str(row[0])
                for row in (
                    await conn.execute(
                        text(
                            "SELECT user_id FROM notification_subscription "
                            "WHERE user_id = ANY(CAST(:ids AS UUID[]))"
                        ),
                        {"ids": rows.user_ids},
                    )
                ).all()
            }
            system_permission_count = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM role_permission AS rp "
                        "JOIN role AS r ON r.id = rp.role_id "
                        "WHERE r.is_system = true"
                    )
                )
            ).scalar_one()

        expected_roles = {rows.role_ids[index]}
        assert visible_roles == expected_roles
        assert visible_subscriptions == {rows.user_ids[index]}
        assert system_permission_count > 0

    async with app_engine_iso.begin() as conn:
        no_context_counts = (
            (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM role_permission "
                        "WHERE role_id = ANY(CAST(:ids AS UUID[]))"
                    ),
                    {"ids": rows.role_ids},
                )
            ).scalar_one(),
            (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM notification_subscription "
                        "WHERE user_id = ANY(CAST(:ids AS UUID[]))"
                    ),
                    {"ids": rows.user_ids},
                )
            ).scalar_one(),
        )
    assert no_context_counts == (0, 0)

    async with support_engine_iso.begin() as conn:
        support_counts = (
            (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM role_permission "
                        "WHERE role_id = ANY(CAST(:ids AS UUID[]))"
                    ),
                    {"ids": rows.role_ids},
                )
            ).scalar_one(),
            (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM notification_subscription "
                        "WHERE user_id = ANY(CAST(:ids AS UUID[]))"
                    ),
                    {"ids": rows.user_ids},
                )
            ).scalar_one(),
            (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM notification_delivery "
                        "WHERE id = ANY(CAST(:ids AS UUID[]))"
                    ),
                    {"ids": rows.delivery_ids},
                )
            ).scalar_one(),
        )
    assert support_counts == (7, 3, 2)


async def test_identity_directory_and_notifications_are_recipient_scoped(
    app_engine_iso: AsyncEngine,
    indirect_scope_rows: IndirectScopeRows,
) -> None:
    rows = indirect_scope_rows
    async with app_engine_iso.begin() as conn:
        await _set_app_context(
            conn,
            tenant_id=rows.tenant_ids[0],
            user_id=rows.user_ids[0],
        )
        directory_user_ids = {
            str(row[0])
            for row in (
                await conn.execute(
                    text("SELECT id FROM app_user " "WHERE id = ANY(CAST(:ids AS UUID[]))"),
                    {"ids": rows.user_ids},
                )
            ).all()
        }
        visible_notification_ids = {
            str(row[0])
            for row in (
                await conn.execute(
                    text("SELECT id FROM notification " "WHERE id = ANY(CAST(:ids AS UUID[]))"),
                    {"ids": rows.notification_ids},
                )
            ).all()
        }
        marked = (
            await conn.execute(
                text(
                    "SELECT public.mark_scoped_notification_read("
                    ":notification_id, :user_id, pg_catalog.now())"
                ),
                {
                    "notification_id": rows.notification_ids[0],
                    "user_id": rows.user_ids[0],
                },
            )
        ).scalar_one()

    assert directory_user_ids == {
        rows.user_ids[0],
        rows.user_ids[2],
        rows.user_ids[3],
    }
    assert visible_notification_ids == {rows.notification_ids[0]}
    assert marked == 1

    await _assert_rls_denied(
        app_engine_iso,
        tenant_id=rows.tenant_ids[0],
        user_id=rows.user_ids[0],
        statement=(
            "SELECT public.mark_scoped_notification_read("
            ":notification_id, :target_user_id, pg_catalog.now())"
        ),
        params={
            "notification_id": rows.notification_ids[2],
            "target_user_id": rows.user_ids[2],
        },
    )


async def test_runtime_cannot_write_identity_assignment_or_notification_tables(
    app_engine_iso: AsyncEngine,
    indirect_scope_rows: IndirectScopeRows,
) -> None:
    rows = indirect_scope_rows
    statements = (
        (
            "SELECT password_hash FROM app_user WHERE id = :user_id",
            {"user_id": rows.user_ids[0]},
        ),
        (
            "INSERT INTO app_user (email, full_name, home_tenant_id) "
            "VALUES (:email, 'Blocked direct insert', :tenant_id)",
            {
                "email": f"blocked-direct-{rows.token}@example.invalid",
                "tenant_id": rows.tenant_ids[0],
            },
        ),
        (
            "UPDATE app_user SET is_developer = true WHERE id = :user_id",
            {"user_id": rows.user_ids[0]},
        ),
        (
            "DELETE FROM app_user WHERE id = :user_id",
            {"user_id": rows.user_ids[2]},
        ),
        (
            "INSERT INTO user_assignment (user_id, tenant_id, role_id) "
            "VALUES (:user_id, :tenant_id, :role_id)",
            {
                "user_id": rows.user_ids[3],
                "tenant_id": rows.tenant_ids[0],
                "role_id": rows.role_ids[0],
            },
        ),
        (
            "UPDATE user_assignment SET is_active = false " "WHERE user_id = :user_id",
            {"user_id": rows.user_ids[0]},
        ),
        (
            "DELETE FROM user_assignment WHERE user_id = :user_id",
            {"user_id": rows.user_ids[0]},
        ),
        (
            "INSERT INTO notification (tenant_id, user_id, event_type, title) "
            "VALUES (:tenant_id, :user_id, 'security.direct', 'Blocked')",
            {
                "tenant_id": rows.tenant_ids[0],
                "user_id": rows.user_ids[0],
            },
        ),
        (
            "UPDATE notification SET read_at = pg_catalog.now() " "WHERE id = :notification_id",
            {"notification_id": rows.notification_ids[0]},
        ),
        (
            "DELETE FROM notification WHERE id = :notification_id",
            {"notification_id": rows.notification_ids[0]},
        ),
    )

    for statement, params in statements:
        await _assert_rls_denied(
            app_engine_iso,
            tenant_id=rows.tenant_ids[0],
            user_id=rows.user_ids[0],
            statement=statement,
            params=params,
        )


async def test_auth_lookup_resolves_password_requirement_without_tenant_context(
    support_engine_iso: AsyncEngine,
    app_engine_iso: AsyncEngine,
    indirect_scope_rows: IndirectScopeRows,
) -> None:
    rows = indirect_scope_rows
    email = f"rls-user-a-{rows.token}@example.invalid"
    salt = uuid4().hex
    candidate_hash = hash_code("123456", salt)

    try:
        async with app_engine_iso.begin() as conn:
            await conn.execute(
                text(
                    "SELECT public.issue_auth_email_code("
                    ":email, :candidate_hash, :salt, '127.0.0.1', NULL)"
                ),
                {
                    "email": email,
                    "candidate_hash": candidate_hash,
                    "salt": salt,
                },
            )
            code_id = (
                await conn.execute(
                    text("SELECT id FROM public.find_auth_email_code_challenge(:email)"),
                    {"email": email},
                )
            ).scalar_one()
            login_record = (
                (
                    await conn.execute(
                        text(
                            "SELECT id, status, password_required "
                            "FROM public.lookup_login_user_by_email("
                            ":email, :code_id, :candidate_hash)"
                        ),
                        {
                            "email": email,
                            "code_id": code_id,
                            "candidate_hash": candidate_hash,
                        },
                    )
                )
                .mappings()
                .one()
            )
    finally:
        async with support_engine_iso.begin() as conn:
            await conn.execute(
                text("DELETE FROM login_attempt WHERE email_lower = :email"),
                {"email": email},
            )
            await conn.execute(
                text("DELETE FROM email_code WHERE email_lower = :email"),
                {"email": email},
            )

    assert str(login_record["id"]) == rows.user_ids[0]
    assert login_record["status"] == "invited"
    assert login_record["password_required"] is True

    async with app_engine_iso.connect() as conn:
        transaction = await conn.begin()
        try:
            with pytest.raises(DBAPIError) as raised:
                await conn.execute(
                    text("SELECT * FROM public.lookup_auth_user_by_id(" ":user_id, NULL)"),
                    {"user_id": rows.user_ids[0]},
                )
            assert _sqlstate(raised.value) == "42501"
        finally:
            if transaction.is_active:
                await transaction.rollback()

    async with app_engine_iso.begin() as conn:
        await _set_app_context(
            conn,
            tenant_id=rows.tenant_ids[0],
            user_id=rows.user_ids[0],
        )
        own_identity = (
            await conn.execute(
                text("SELECT id FROM public.lookup_auth_user_by_id(" ":user_id, NULL)"),
                {"user_id": rows.user_ids[0]},
            )
        ).scalar_one()

    assert str(own_identity) == rows.user_ids[0]


async def test_runtime_assignment_function_blocks_system_role_escalation(
    support_engine_iso: AsyncEngine,
    app_engine_iso: AsyncEngine,
    indirect_scope_rows: IndirectScopeRows,
) -> None:
    rows = indirect_scope_rows
    async with support_engine_iso.begin() as conn:
        system_role_id = str(
            (
                await conn.execute(
                    text("SELECT id FROM role " "WHERE is_system AND name = 'developer'")
                )
            ).scalar_one()
        )

    await _assert_rls_denied(
        app_engine_iso,
        tenant_id=rows.tenant_ids[0],
        user_id=rows.user_ids[0],
        statement=(
            "SELECT * FROM public.create_tenant_user_assignment("
            ":tenant_id, :target_user_id, NULL, :role_id, false)"
        ),
        params={
            "tenant_id": rows.tenant_ids[0],
            "target_user_id": rows.user_ids[3],
            "role_id": system_role_id,
        },
    )
    await _assert_rls_denied(
        app_engine_iso,
        tenant_id=rows.tenant_ids[0],
        user_id=rows.user_ids[0],
        statement="SELECT public.find_invitable_user_id(:tenant_id, :email)",
        params={
            "tenant_id": rows.tenant_ids[0],
            "email": f"rls-member-{rows.token}@example.invalid",
        },
    )

    async with app_engine_iso.begin() as conn:
        await _set_app_context(
            conn,
            tenant_id=rows.tenant_ids[0],
            user_id=rows.user_ids[0],
        )
        assignment = (
            (
                await conn.execute(
                    text(
                        "SELECT user_id, tenant_id, role_id "
                        "FROM public.create_tenant_user_assignment("
                        ":tenant_id, :target_user_id, NULL, :role_id, false)"
                    ),
                    {
                        "tenant_id": rows.tenant_ids[0],
                        "target_user_id": rows.user_ids[3],
                        "role_id": rows.role_ids[2],
                    },
                )
            )
            .mappings()
            .one()
        )

    assert str(assignment["user_id"]) == rows.user_ids[3]
    assert str(assignment["tenant_id"]) == rows.tenant_ids[0]
    assert str(assignment["role_id"]) == rows.role_ids[2]


async def test_runtime_assignment_requires_tenant_wide_actor_scope(
    support_engine_iso: AsyncEngine,
    app_engine_iso: AsyncEngine,
    indirect_scope_rows: IndirectScopeRows,
) -> None:
    rows = indirect_scope_rows
    async with support_engine_iso.begin() as conn:
        branch_ids = [
            str(row[0])
            for row in (
                await conn.execute(
                    text(
                        "INSERT INTO branch (tenant_id, name) VALUES "
                        "(:tenant_id, :branch_a), (:tenant_id, :branch_b) RETURNING id"
                    ),
                    {
                        "tenant_id": rows.tenant_ids[0],
                        "branch_a": f"Scope A {rows.token}",
                        "branch_b": f"Scope B {rows.token}",
                    },
                )
            ).all()
        ]
        await conn.execute(
            text(
                "UPDATE user_assignment SET branch_id = :branch_id "
                "WHERE tenant_id = :tenant_id AND user_id = :user_id"
            ),
            {
                "branch_id": branch_ids[0],
                "tenant_id": rows.tenant_ids[0],
                "user_id": rows.user_ids[0],
            },
        )

    for forbidden_branch_id in (None, branch_ids[0], branch_ids[1]):
        await _assert_rls_denied(
            app_engine_iso,
            tenant_id=rows.tenant_ids[0],
            user_id=rows.user_ids[0],
            statement=(
                "SELECT * FROM public.create_tenant_user_assignment("
                ":tenant_id, :target_user_id, :branch_id, :role_id, false)"
            ),
            params={
                "tenant_id": rows.tenant_ids[0],
                "target_user_id": rows.user_ids[3],
                "branch_id": forbidden_branch_id,
                "role_id": rows.role_ids[2],
            },
        )


async def test_blocking_user_revokes_sessions_immediately(
    support_engine_iso: AsyncEngine,
    app_engine_iso: AsyncEngine,
    indirect_scope_rows: IndirectScopeRows,
) -> None:
    rows = indirect_scope_rows
    refresh_hash = hash_token(f"security-block-{rows.token}")
    async with support_engine_iso.begin() as conn:
        session_id = str(
            (
                await conn.execute(
                    text(
                        "INSERT INTO session "
                        "(user_id, refresh_token_hash, expires_at) "
                        "VALUES (:user_id, :refresh_hash, "
                        "pg_catalog.now() + INTERVAL '1 day') RETURNING id"
                    ),
                    {
                        "user_id": rows.user_ids[2],
                        "refresh_hash": refresh_hash,
                    },
                )
            ).scalar_one()
        )

    async with app_engine_iso.begin() as conn:
        await _set_app_context(
            conn,
            tenant_id=rows.tenant_ids[0],
            user_id=rows.user_ids[0],
        )
        changed = (
            await conn.execute(
                text(
                    "SELECT public.set_tenant_membership_status("
                    ":tenant_id, :target_user_id, 'suspended', pg_catalog.now())"
                ),
                {
                    "tenant_id": rows.tenant_ids[0],
                    "target_user_id": rows.user_ids[2],
                },
            )
        ).scalar_one()

    async with support_engine_iso.begin() as conn:
        state = (
            (
                await conn.execute(
                    text(
                        "SELECT membership.status, auth_session.revoked_at, "
                        "auth_session.revoked_reason "
                        "FROM tenant_membership AS membership "
                        "JOIN session AS auth_session "
                        "ON auth_session.user_id = membership.user_id "
                        "WHERE membership.tenant_id = :tenant_id "
                        "AND membership.user_id = :user_id "
                        "AND auth_session.id = :session_id"
                    ),
                    {
                        "tenant_id": rows.tenant_ids[0],
                        "user_id": rows.user_ids[2],
                        "session_id": session_id,
                    },
                )
            )
            .mappings()
            .one()
        )

    assert changed is True
    assert state["status"] == "suspended"
    assert state["revoked_at"] is not None
    assert state["revoked_reason"] == "membership_suspended"


async def test_administrative_session_revocation_is_tenant_scoped_and_audited(
    support_engine_iso: AsyncEngine,
    app_engine_iso: AsyncEngine,
    indirect_scope_rows: IndirectScopeRows,
) -> None:
    rows = indirect_scope_rows
    target_session_ids: list[str] = []
    async with support_engine_iso.begin() as conn:
        for index in range(2):
            target_session_ids.append(
                str(
                    (
                        await conn.execute(
                            text(
                                "INSERT INTO session "
                                "(user_id, refresh_token_hash, expires_at) "
                                "VALUES (:user_id, :refresh_hash, "
                                "pg_catalog.now() + INTERVAL '1 day') RETURNING id"
                            ),
                            {
                                "user_id": rows.user_ids[2],
                                "refresh_hash": hash_token(f"tenant-revoke-{index}-{rows.token}"),
                            },
                        )
                    ).scalar_one()
                )
            )
        outsider_session_id = str(
            (
                await conn.execute(
                    text(
                        "INSERT INTO session "
                        "(user_id, refresh_token_hash, expires_at) "
                        "VALUES (:user_id, :refresh_hash, "
                        "pg_catalog.now() + INTERVAL '1 day') RETURNING id"
                    ),
                    {
                        "user_id": rows.user_ids[1],
                        "refresh_hash": hash_token(f"outsider-revoke-{rows.token}"),
                    },
                )
            ).scalar_one()
        )
        await conn.execute(
            text(
                "INSERT INTO tenant_ownership (tenant_id, membership_id) "
                "SELECT :tenant_id, id FROM tenant_membership "
                "WHERE tenant_id = :tenant_id AND user_id = :user_id"
            ),
            {
                "tenant_id": rows.tenant_ids[0],
                "user_id": rows.user_ids[3],
            },
        )

    async with app_engine_iso.begin() as conn:
        await _set_app_context(
            conn,
            tenant_id=rows.tenant_ids[0],
            user_id=rows.user_ids[0],
        )
        revoked = (
            (
                await conn.execute(
                    text(
                        "SELECT * FROM public.revoke_tenant_user_auth_sessions("
                        ":tenant_id, :target_user_id)"
                    ),
                    {
                        "tenant_id": rows.tenant_ids[0],
                        "target_user_id": rows.user_ids[2],
                    },
                )
            )
            .mappings()
            .one()
        )
        outsider = (
            (
                await conn.execute(
                    text(
                        "SELECT * FROM public.revoke_tenant_user_auth_sessions("
                        ":tenant_id, :target_user_id)"
                    ),
                    {
                        "tenant_id": rows.tenant_ids[0],
                        "target_user_id": rows.user_ids[1],
                    },
                )
            )
            .mappings()
            .one()
        )
        protected_owner = (
            (
                await conn.execute(
                    text(
                        "SELECT * FROM public.revoke_tenant_user_auth_sessions("
                        ":tenant_id, :target_user_id)"
                    ),
                    {
                        "tenant_id": rows.tenant_ids[0],
                        "target_user_id": rows.user_ids[3],
                    },
                )
            )
            .mappings()
            .one()
        )

    assert dict(revoked) == {"result": "revoked", "revoked_count": 2}
    assert dict(outsider) == {"result": "not_found", "revoked_count": 0}
    assert dict(protected_owner) == {"result": "protected", "revoked_count": 0}

    async with support_engine_iso.begin() as conn:
        fresh_target_session_id = str(
            (
                await conn.execute(
                    text(
                        "INSERT INTO session "
                        "(user_id, refresh_token_hash, expires_at) "
                        "VALUES (:user_id, :refresh_hash, "
                        "pg_catalog.now() + INTERVAL '1 day') RETURNING id"
                    ),
                    {
                        "user_id": rows.user_ids[2],
                        "refresh_hash": hash_token(f"unauthorized-revoke-{rows.token}"),
                    },
                )
            ).scalar_one()
        )

    await _assert_rls_denied(
        app_engine_iso,
        tenant_id=rows.tenant_ids[0],
        user_id=rows.user_ids[3],
        statement=(
            "SELECT * FROM public.revoke_tenant_user_auth_sessions(" ":tenant_id, :target_user_id)"
        ),
        params={
            "tenant_id": rows.tenant_ids[0],
            "target_user_id": rows.user_ids[2],
        },
    )

    async with support_engine_iso.begin() as conn:
        session_rows = (
            (
                await conn.execute(
                    text(
                        "SELECT id, revoked_at, revoked_reason FROM session "
                        "WHERE id = ANY(CAST(:session_ids AS UUID[]))"
                    ),
                    {
                        "session_ids": [
                            *target_session_ids,
                            outsider_session_id,
                            fresh_target_session_id,
                        ]
                    },
                )
            )
            .mappings()
            .all()
        )
        audit_row = (
            (
                await conn.execute(
                    text(
                        "SELECT user_id, tenant_id, record_id, metadata "
                        "FROM audit_log "
                        "WHERE action = 'UPDATE' "
                        "AND table_name = 'session' "
                        "AND record_id = :target_user_id "
                        "AND metadata ->> 'event' = 'tenant_user_sessions_revoked' "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"target_user_id": rows.user_ids[2]},
                )
            )
            .mappings()
            .one()
        )

    by_id = {str(row["id"]): row for row in session_rows}
    assert all(
        by_id[session_id]["revoked_reason"] == "tenant_admin_revoked"
        for session_id in target_session_ids
    )
    assert by_id[outsider_session_id]["revoked_at"] is None
    assert by_id[fresh_target_session_id]["revoked_at"] is None
    assert str(audit_row["user_id"]) == rows.user_ids[0]
    assert str(audit_row["tenant_id"]) == rows.tenant_ids[0]
    assert str(audit_row["record_id"]) == rows.user_ids[2]
    assert audit_row["metadata"]["revoked_count"] == 2


async def test_role_and_subscription_writes_are_isolated(
    app_engine_iso: AsyncEngine,
    indirect_scope_rows: IndirectScopeRows,
) -> None:
    rows = indirect_scope_rows
    async with app_engine_iso.begin() as conn:
        await _set_app_context(
            conn,
            tenant_id=rows.tenant_ids[0],
            user_id=rows.user_ids[0],
        )
        await conn.execute(
            text(
                "INSERT INTO role_permission (role_id, permission_code) "
                "VALUES (:role_id, :permission_code)"
            ),
            {"role_id": rows.role_ids[2], "permission_code": rows.permission_codes[1]},
        )
        await conn.execute(
            text(
                "INSERT INTO notification_subscription "
                "(user_id, event_type) VALUES (:user_id, 'security.rls.own')"
            ),
            {"user_id": rows.user_ids[0]},
        )

    denied_statements = (
        (
            "INSERT INTO role_permission (role_id, permission_code) "
            "VALUES (:role_id, :permission_code)",
            {"role_id": rows.role_ids[1], "permission_code": rows.permission_codes[1]},
        ),
        (
            "INSERT INTO notification_subscription (user_id, event_type) "
            "VALUES (:user_id, 'security.rls.cross')",
            {"user_id": rows.user_ids[1]},
        ),
        (
            "UPDATE role SET tenant_id = NULL WHERE id = :role_id",
            {"role_id": rows.role_ids[0]},
        ),
        (
            "UPDATE role SET is_system = true WHERE id = :role_id",
            {"role_id": rows.role_ids[0]},
        ),
        (
            "INSERT INTO role (tenant_id, name, level, is_system) "
            "VALUES (NULL, :name, 4, false)",
            {"name": f"global-role-{rows.token}"},
        ),
    )
    for statement, params in denied_statements:
        await _assert_rls_denied(
            app_engine_iso,
            tenant_id=rows.tenant_ids[0],
            user_id=rows.user_ids[0],
            statement=statement,
            params=params,
        )

    async with app_engine_iso.begin() as conn:
        await _set_app_context(
            conn,
            tenant_id=rows.tenant_ids[0],
            user_id=rows.user_ids[0],
        )
        system_role_id = (
            await conn.execute(
                text("SELECT id FROM role WHERE is_system AND name = 'administrator'")
            )
        ).scalar_one()
        system_update = await conn.execute(
            text("UPDATE role SET description = 'blocked' WHERE id = :role_id"),
            {"role_id": system_role_id},
        )
        system_delete = await conn.execute(
            text("DELETE FROM role WHERE id = :role_id"),
            {"role_id": system_role_id},
        )
        cross_role_permission_delete = await conn.execute(
            text(
                "DELETE FROM role_permission "
                "WHERE role_id = :role_id AND permission_code = :permission_code"
            ),
            {
                "role_id": rows.role_ids[1],
                "permission_code": rows.permission_codes[0],
            },
        )
        cross_subscription_delete = await conn.execute(
            text(
                "DELETE FROM notification_subscription "
                "WHERE user_id = :user_id AND event_type = 'security.rls.seed'"
            ),
            {"user_id": rows.user_ids[1]},
        )
        assert system_update.rowcount == 0
        assert system_delete.rowcount == 0
        assert cross_role_permission_delete.rowcount == 0
        assert cross_subscription_delete.rowcount == 0


async def test_role_mutations_recheck_live_owner_scope(
    support_engine_iso: AsyncEngine,
    app_engine_iso: AsyncEngine,
    indirect_scope_rows: IndirectScopeRows,
) -> None:
    rows = indirect_scope_rows

    await _assert_rls_denied(
        app_engine_iso,
        tenant_id=rows.tenant_ids[0],
        user_id=rows.user_ids[0],
        statement=(
            "UPDATE role SET description = :description, version = version + 1 "
            "WHERE id = :role_id"
        ),
        params={
            "description": "self-role mutation",
            "role_id": rows.role_ids[0],
        },
    )

    async with support_engine_iso.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM role_permission "
                "WHERE role_id = :role_id AND permission_code = :permission_code"
            ),
            {
                "role_id": rows.role_ids[0],
                "permission_code": rows.permission_codes[1],
            },
        )

    await _assert_rls_denied(
        app_engine_iso,
        tenant_id=rows.tenant_ids[0],
        user_id=rows.user_ids[0],
        statement=(
            "INSERT INTO role_permission (role_id, permission_code) "
            "VALUES (:role_id, :permission_code)"
        ),
        params={
            "role_id": rows.role_ids[2],
            "permission_code": rows.permission_codes[1],
        },
    )


async def test_notification_functions_support_same_tenant_recipient(
    support_engine_iso: AsyncEngine,
    app_engine_iso: AsyncEngine,
    indirect_scope_rows: IndirectScopeRows,
) -> None:
    rows = indirect_scope_rows
    async with app_engine_iso.begin() as conn:
        await _set_app_context(
            conn,
            tenant_id=rows.tenant_ids[0],
            user_id=rows.user_ids[0],
        )
        async with AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        ) as session:
            service = NotificationsService(NotificationsRepository(session))
            notification = await service.notify(
                tenant_id=UUID(rows.tenant_ids[0]),
                user_id=UUID(rows.user_ids[2]),
                event_type="security.rls.recipient",
                title="RLS same-tenant notification",
            )
            assert notification is not None
            notification_id = str(notification.id)
            await session.commit()

    async with app_engine_iso.begin() as conn:
        await _set_app_context(
            conn,
            tenant_id=rows.tenant_ids[0],
            user_id=rows.user_ids[0],
        )
        duplicate_delivery_id = str(
            (
                await conn.execute(
                    text(
                        "SELECT public.enqueue_notification_delivery(" ":notification_id, 'email')"
                    ),
                    {"notification_id": notification_id},
                )
            ).scalar_one()
        )

    async with support_engine_iso.begin() as conn:
        delivery = (
            await conn.execute(
                text(
                    "SELECT id, notification_id, recipient "
                    "FROM notification_delivery WHERE notification_id = :notification_id"
                ),
                {"notification_id": notification_id},
            )
        ).one()
        await conn.execute(
            text(
                "UPDATE notification_subscription SET is_enabled = false "
                "WHERE user_id = :user_id AND event_type = 'security.rls.recipient'"
            ),
            {"user_id": rows.user_ids[2]},
        )
    assert str(delivery.id) == duplicate_delivery_id
    assert str(delivery.notification_id) == notification_id
    assert delivery.recipient == f"rls-recipient-{rows.token}@example.invalid"

    await _assert_rls_denied(
        app_engine_iso,
        tenant_id=rows.tenant_ids[0],
        user_id=rows.user_ids[0],
        statement=(
            "SELECT * FROM public.resolve_notification_subscription("
            ":tenant_id, :user_id, 'security.rls.seed')"
        ),
        params={"tenant_id": rows.tenant_ids[0], "user_id": rows.user_ids[1]},
    )
    await _assert_rls_denied(
        app_engine_iso,
        tenant_id=rows.tenant_ids[0],
        user_id=rows.user_ids[0],
        statement="SELECT public.enqueue_notification_delivery(:notification_id, 'email')",
        params={"notification_id": rows.notification_ids[1]},
    )
    await _assert_rls_denied(
        app_engine_iso,
        tenant_id=rows.tenant_ids[0],
        user_id=rows.user_ids[0],
        statement="SELECT public.enqueue_notification_delivery(:notification_id, 'email')",
        params={"notification_id": notification_id},
    )
    await _assert_rls_denied(
        app_engine_iso,
        tenant_id=rows.tenant_ids[0],
        user_id=rows.user_ids[0],
        statement="SELECT public.enqueue_notification_delivery(:notification_id, 'email')",
        params={"notification_id": rows.notification_ids[0]},
    )
    await _assert_rls_denied(
        app_engine_iso,
        tenant_id=rows.tenant_ids[0],
        user_id=rows.user_ids[0],
        statement="SELECT public.enqueue_notification_delivery(:notification_id, 'sms')",
        params={"notification_id": rows.notification_ids[0]},
        expected_sqlstate="22023",
    )


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT id FROM notification_delivery LIMIT 1",
        "INSERT INTO notification_delivery (notification_id, channel, recipient) "
        "VALUES (:notification_id, 'email', 'blocked@example.invalid')",
        "UPDATE notification_delivery SET status = 'sent' WHERE id = :delivery_id",
        "DELETE FROM notification_delivery WHERE id = :delivery_id",
    ],
)
async def test_runtime_has_no_direct_outbox_access(
    statement: str,
    app_engine_iso: AsyncEngine,
    indirect_scope_rows: IndirectScopeRows,
) -> None:
    rows = indirect_scope_rows
    await _assert_rls_denied(
        app_engine_iso,
        tenant_id=rows.tenant_ids[0],
        user_id=rows.user_ids[0],
        statement=statement,
        params={
            "notification_id": rows.notification_ids[0],
            "delivery_id": rows.delivery_ids[0],
        },
    )
