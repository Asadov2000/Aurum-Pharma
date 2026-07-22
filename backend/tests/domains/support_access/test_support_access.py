"""Short-lived tenant support access is explicit, scoped, and revocable."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from starlette.requests import Request

from app.core.config import get_settings
from app.core.deps import _seed_request_db_context, get_db
from app.core.errors import PermissionDeniedError
from app.core.security import decode_access_token, hash_token
from app.domains.audit.models import AuditLog
from app.domains.auth.models import AppUser
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.support_access.repository import SupportAccessRepository
from app.domains.support_access.service import SupportAccessService
from app.main import app
from tests.auth_helpers import create_support_access_token


async def _auth_session_id(db: AsyncSession, actor: AppUser) -> tuple[str, UUID]:
    token = await create_support_access_token(db, actor)
    return token, UUID(str(decode_access_token(token)["sid"]))


async def _tenant(db: AsyncSession):  # type: ignore[no-untyped-def]
    suffix = uuid4().hex[:8]
    return await FoundationService(FoundationRepository(db)).create_tenant(
        payload={
            "name": f"Support tenant {suffix}",
            "contact_email": f"support-{suffix}@aurum.tj",
        }
    )


async def _support_user(db: AsyncSession, *, developer: bool = False) -> AppUser:
    suffix = uuid4().hex[:8]
    user = AppUser(
        email=f"support-{suffix}@aurum.tj",
        full_name="Support Operator",
        is_developer=developer,
        is_administrator=not developer,
        status="active",
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def test_session_is_scoped_audited_and_immediately_revocable(
    db_session: AsyncSession,
) -> None:
    tenant = await _tenant(db_session)
    actor = await _support_user(db_session)
    _token, actor_session_id = await _auth_session_id(db_session, actor)
    service = SupportAccessService(SupportAccessRepository(db_session))

    session = await service.start_session(
        actor_user_id=actor.id,
        actor_session_id=actor_session_id,
        actor_is_developer=False,
        actor_is_administrator=True,
        tenant_id=tenant.id,
        reason="Настройка ролей перед пилотным запуском",
        duration_minutes=15,
        requested_capabilities=["users.view", "users.block", "roles.create", "roles.update"],
    )

    assert session.tenant_id == tenant.id
    assert session.capabilities == ("roles.create", "roles.update", "users.block", "users.view")
    assert session.is_read_only is False
    audit = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.action == "IMPERSONATE",
                    AuditLog.record_id == tenant.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert any(
        entry.metadata_json is not None
        and entry.metadata_json.get("event") == "support_access_started"
        and "email" not in entry.metadata_json
        for entry in audit
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/roles",
            "headers": [],
        }
    )
    request.state.user_id = actor.id
    request.state.tenant_id = None
    request.state.support_access_session_id = session.id
    request.state.auth_session_id = actor_session_id
    request.state.invalid_support_access = False
    request.state.is_support_session = True
    request.state.support_access_resolved = True
    request.state.tenant_id = tenant.id
    await _seed_request_db_context(request, db_session)

    assert await db_session.scalar(text("SELECT public.is_support_session()")) is False
    assert await db_session.scalar(text("SELECT public.is_tenant_support_session()")) is True
    assert (
        await db_session.scalar(text("SELECT public.support_access_has_capability('roles.update')"))
        is True
    )
    assert (
        await db_session.scalar(text("SELECT public.support_access_has_capability('roles.assign')"))
        is False
    )

    await db_session.execute(
        text("""
            UPDATE public.support_access_session
            SET revoked_at = statement_timestamp(), revoked_by_user_id = :actor_id
            WHERE id = :session_id
            """),
        {"session_id": session.id, "actor_id": actor.id},
    )
    assert await db_session.scalar(text("SELECT public.is_support_session()")) is False


async def test_administrator_cannot_request_capability_outside_support_catalog(
    db_session: AsyncSession,
) -> None:
    tenant = await _tenant(db_session)
    actor = await _support_user(db_session)
    _token, actor_session_id = await _auth_session_id(db_session, actor)
    service = SupportAccessService(SupportAccessRepository(db_session))

    with pytest.raises(PermissionDeniedError, match="outside the allowed scope"):
        await service.start_session(
            actor_user_id=actor.id,
            actor_session_id=actor_session_id,
            actor_is_developer=False,
            actor_is_administrator=True,
            tenant_id=tenant.id,
            reason="Попытка получить лишнее полномочие",
            duration_minutes=15,
            requested_capabilities=["tenant.export.full"],
        )


async def test_support_session_is_visible_and_revocable_only_in_its_auth_family(
    db_session: AsyncSession,
) -> None:
    tenant = await _tenant(db_session)
    actor = await _support_user(db_session)
    _first_token, first_auth_session_id = await _auth_session_id(db_session, actor)
    _second_token, second_auth_session_id = await _auth_session_id(db_session, actor)
    service = SupportAccessService(SupportAccessRepository(db_session))

    session = await service.start_session(
        actor_user_id=actor.id,
        actor_session_id=first_auth_session_id,
        actor_is_developer=False,
        actor_is_administrator=True,
        tenant_id=tenant.id,
        reason="Проверка изоляции служебного доступа между устройствами",
        duration_minutes=15,
        requested_capabilities=["users.view"],
    )

    assert [
        item.id
        for item in await service.list_active_sessions(
            actor_user_id=actor.id,
            actor_session_id=first_auth_session_id,
        )
    ] == [session.id]
    assert (
        await service.list_active_sessions(
            actor_user_id=actor.id,
            actor_session_id=second_auth_session_id,
        )
        == []
    )

    await service.revoke_session(
        actor_user_id=actor.id,
        actor_session_id=second_auth_session_id,
        session_id=session.id,
    )
    assert [
        item.id
        for item in await service.list_active_sessions(
            actor_user_id=actor.id,
            actor_session_id=first_auth_session_id,
        )
    ] == [session.id]


async def test_start_endpoint_requires_support_mfa_and_returns_no_bearer_secret(
    db_session: AsyncSession,
    client: AsyncClient,
) -> None:
    tenant = await _tenant(db_session)
    actor = await _support_user(db_session)
    token = await create_support_access_token(db_session, actor)

    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        response = await client.post(
            "/api/v1/admin/support-access/sessions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "tenant_id": str(tenant.id),
                "reason": "Проверка конструктора ролей",
                "duration_minutes": 10,
                "capabilities": ["users.view", "roles.create"],
            },
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["tenant_id"] == str(tenant.id)
    assert payload["capabilities"] == ["roles.create", "users.view"]
    assert "token" not in payload
    assert "email" not in payload


@dataclass(frozen=True)
class _RuntimeSupportScenario:
    tenant_id: UUID
    other_tenant_id: UUID
    actor_id: UUID
    actor_auth_session_id: UUID
    other_auth_session_id: UUID
    support_session_id: UUID
    suffix: str


async def _create_runtime_support_scenario(
    connection: AsyncConnection,
) -> _RuntimeSupportScenario:
    suffix = uuid4().hex[:10]
    tenant_id = (
        await connection.execute(
            text(
                "INSERT INTO public.tenant (name, contact_email) "
                "VALUES (:name, :email) RETURNING id"
            ),
            {
                "name": f"Scoped support {suffix}",
                "email": f"scoped-{suffix}@example.invalid",
            },
        )
    ).scalar_one()
    other_tenant_id = (
        await connection.execute(
            text(
                "INSERT INTO public.tenant (name, contact_email) "
                "VALUES (:name, :email) RETURNING id"
            ),
            {
                "name": f"Other support {suffix}",
                "email": f"other-{suffix}@example.invalid",
            },
        )
    ).scalar_one()
    await connection.execute(text("SELECT set_config('app.support_session', 'true', true)"))
    await connection.execute(
        text("""
            INSERT INTO public.role (tenant_id, name, level)
            VALUES
              (:tenant_id, :tenant_role_name, 4),
              (:other_tenant_id, :other_role_name, 4)
            """),
        {
            "tenant_id": tenant_id,
            "other_tenant_id": other_tenant_id,
            "tenant_role_name": f"Scoped role {suffix}",
            "other_role_name": f"Other role {suffix}",
        },
    )
    actor_id = (
        await connection.execute(
            text(
                "INSERT INTO public.app_user "
                "(email, full_name, is_administrator, status) "
                "VALUES (:email, 'Scoped support', true, 'active') RETURNING id"
            ),
            {"email": f"actor-{suffix}@example.invalid"},
        )
    ).scalar_one()
    auth_sessions = list(
        (
            await connection.execute(
                text("""
                    INSERT INTO public.session (
                      user_id,
                      refresh_token_hash,
                      expires_at,
                      mfa_verified_at
                    ) VALUES
                      (
                        :actor_id,
                        :first_hash,
                        statement_timestamp() + INTERVAL '1 day',
                        statement_timestamp()
                      ),
                      (
                        :actor_id,
                        :second_hash,
                        statement_timestamp() + INTERVAL '1 day',
                        statement_timestamp()
                      )
                    RETURNING id
                    """),
                {
                    "actor_id": actor_id,
                    "first_hash": hash_token(f"support-family-a-{suffix}"),
                    "second_hash": hash_token(f"support-family-b-{suffix}"),
                },
            )
        ).scalars()
    )
    actor_auth_session_id = auth_sessions[0]
    other_auth_session_id = auth_sessions[1]
    await connection.execute(
        text("""
            INSERT INTO public.tenant_membership (
              tenant_id,
              user_id,
              full_name,
              status
            ) VALUES (
              :tenant_id,
              :actor_id,
              'Scoped support tenant member',
              'active'
            )
            """),
        {"tenant_id": tenant_id, "actor_id": actor_id},
    )
    tenant_member_role_id = (
        await connection.execute(
            text("""
                INSERT INTO public.role (tenant_id, name, level)
                VALUES (:tenant_id, :name, 4)
                RETURNING id
                """),
            {
                "tenant_id": tenant_id,
                "name": f"Hybrid member role {suffix}",
            },
        )
    ).scalar_one()
    await connection.execute(
        text("""
            INSERT INTO public.role_permission (role_id, permission_code)
            VALUES (:role_id, 'roles.assign')
            """),
        {"role_id": tenant_member_role_id},
    )
    await connection.execute(
        text("""
            INSERT INTO public.user_assignment (user_id, tenant_id, role_id)
            VALUES (:actor_id, :tenant_id, :role_id)
            """),
        {
            "actor_id": actor_id,
            "tenant_id": tenant_id,
            "role_id": tenant_member_role_id,
        },
    )
    support_session_id = (
        await connection.execute(
            text("""
                INSERT INTO public.support_access_session (
                  tenant_id,
                  actor_user_id,
                  actor_session_id,
                  reason,
                  is_read_only,
                  expires_at,
                  created_by,
                  updated_by
                ) VALUES (
                  :tenant_id,
                  :actor_id,
                  :actor_session_id,
                  'Scoped RLS integration check',
                  false,
                  statement_timestamp() + INTERVAL '15 minutes',
                  :actor_id,
                  :actor_id
                )
                RETURNING id
                """),
            {
                "tenant_id": tenant_id,
                "actor_id": actor_id,
                "actor_session_id": actor_auth_session_id,
            },
        )
    ).scalar_one()
    await connection.execute(
        text("""
            INSERT INTO public.support_access_capability (
              support_access_session_id,
              tenant_id,
              permission_code,
              created_by
            )
            SELECT :session_id, :tenant_id, code, :actor_id
            FROM unnest(
              ARRAY['branches.view', 'roles.create', 'roles.update', 'users.view']
            ) AS code
            """),
        {
            "session_id": support_session_id,
            "tenant_id": tenant_id,
            "actor_id": actor_id,
        },
    )
    return _RuntimeSupportScenario(
        tenant_id=tenant_id,
        other_tenant_id=other_tenant_id,
        actor_id=actor_id,
        actor_auth_session_id=actor_auth_session_id,
        other_auth_session_id=other_auth_session_id,
        support_session_id=support_session_id,
        suffix=suffix,
    )


def _support_request(
    scenario: _RuntimeSupportScenario,
    *,
    auth_session_id: UUID,
) -> Request:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/roles/users",
            "headers": [],
        }
    )
    request.state.user_id = scenario.actor_id
    request.state.tenant_id = None
    request.state.auth_session_id = auth_session_id
    request.state.support_access_session_id = scenario.support_session_id
    request.state.invalid_support_access = False
    request.state.is_support_session = False
    return request


async def _retire_runtime_support_actor(
    connection: AsyncConnection,
    scenario: _RuntimeSupportScenario,
) -> None:
    await connection.execute(
        text("""
            UPDATE public.session
            SET
              revoked_at = COALESCE(revoked_at, statement_timestamp()),
              revoked_reason = COALESCE(revoked_reason, 'test_cleanup')
            WHERE user_id = :actor_id
            """),
        {"actor_id": scenario.actor_id},
    )
    await connection.execute(
        text("""
            UPDATE public.app_user
            SET status = 'archived', is_administrator = false
            WHERE id = :actor_id
            """),
        {"actor_id": scenario.actor_id},
    )


async def test_tenant_support_queries_use_app_rls_and_stop_after_revocation() -> None:
    settings = get_settings()
    support_engine = create_async_engine(
        settings.DATABASE_URL_SUPPORT,
        poolclass=NullPool,
    )
    app_engine = create_async_engine(
        settings.DATABASE_URL_APP,
        poolclass=NullPool,
    )
    scenario: _RuntimeSupportScenario | None = None
    try:
        async with support_engine.begin() as connection:
            scenario = await _create_runtime_support_scenario(connection)
        assert scenario is not None
        tenant_id = scenario.tenant_id
        actor_id = scenario.actor_id
        actor_auth_session_id = scenario.actor_auth_session_id
        other_auth_session_id = scenario.other_auth_session_id
        support_session_id = scenario.support_session_id
        suffix = scenario.suffix
        wrong_family_request = _support_request(
            scenario,
            auth_session_id=other_auth_session_id,
        )

        factory = async_sessionmaker(app_engine, expire_on_commit=False)
        async with factory() as wrong_family_session, wrong_family_session.begin():
            with pytest.raises(PermissionDeniedError, match="expired, revoked, or unavailable"):
                await _seed_request_db_context(wrong_family_request, wrong_family_session)

        request = _support_request(
            scenario,
            auth_session_id=actor_auth_session_id,
        )
        async with factory() as app_session, app_session.begin():
            await _seed_request_db_context(request, app_session)
            assert await app_session.scalar(text("SELECT session_user")) == "aurum_app"
            assert await app_session.scalar(text("SELECT public.is_support_session()")) is False
            assert (
                await app_session.scalar(text("SELECT public.is_tenant_support_session()")) is True
            )
            visible_role_tenants = set(
                await app_session.scalars(
                    text("SELECT tenant_id FROM public.role WHERE name LIKE :pattern"),
                    {"pattern": f"%role {suffix}"},
                )
            )
            assert visible_role_tenants == {tenant_id}
            assert scenario.other_tenant_id not in visible_role_tenants
            assert (
                await app_session.scalar(
                    text(
                        "SELECT public.tenant_actor_has_scoped_permission("
                        ":tenant_id, 'roles.assign', NULL::UUID)"
                    ),
                    {"tenant_id": tenant_id},
                )
                is False
            )

            role_id = (
                await app_session.execute(
                    text("""
                        INSERT INTO public.role (
                          tenant_id,
                          name,
                          level,
                          created_by,
                          updated_by
                        ) VALUES (
                          :tenant_id,
                          :name,
                          4,
                          :actor_id,
                          :actor_id
                        )
                        RETURNING id
                        """),
                    {
                        "tenant_id": tenant_id,
                        "name": f"Scoped builder {suffix}",
                        "actor_id": actor_id,
                    },
                )
            ).scalar_one()
            with pytest.raises(DBAPIError) as forbidden:
                async with app_session.begin_nested():
                    await app_session.execute(
                        text("""
                            INSERT INTO public.role_permission (role_id, permission_code)
                            VALUES (:role_id, 'tenant.export.full')
                            """),
                        {"role_id": role_id},
                    )
            assert getattr(forbidden.value.orig, "sqlstate", None) == "42501"

            async with support_engine.begin() as connection:
                await connection.execute(
                    text("""
                        UPDATE public.tenant
                        SET status = 'archived'
                        WHERE id = :tenant_id
                        """),
                    {"tenant_id": tenant_id},
                )
                revoked_at = await connection.scalar(
                    text(
                        "SELECT revoked_at FROM public.support_access_session "
                        "WHERE id = :session_id"
                    ),
                    {"session_id": support_session_id},
                )
                assert revoked_at is not None

            assert (
                await app_session.scalar(text("SELECT public.is_tenant_support_session()")) is False
            )
            assert await app_session.scalar(text("SELECT public.current_tenant_id()")) is None
            assert (
                list(
                    await app_session.scalars(
                        text("SELECT tenant_id FROM public.role WHERE name LIKE :pattern"),
                        {"pattern": f"%role {suffix}"},
                    )
                )
                == []
            )
    finally:
        if scenario is not None:
            async with support_engine.begin() as connection:
                await connection.execute(
                    text("SELECT set_config('app.support_session', 'true', true)")
                )
                await connection.execute(
                    text("DELETE FROM public.tenant WHERE id = ANY(:tenant_ids)"),
                    {
                        "tenant_ids": [
                            scenario.tenant_id,
                            scenario.other_tenant_id,
                        ]
                    },
                )
                await _retire_runtime_support_actor(connection, scenario)
        await app_engine.dispose()
        await support_engine.dispose()
