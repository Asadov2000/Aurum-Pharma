"""Test helpers for roles that must satisfy the published-version contract."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_token
from app.domains.auth.models import AppUser, Session
from app.domains.roles.models import (
    Role,
    RolePermission,
    TenantMembership,
    TenantOwnership,
)
from app.domains.roles.repository import RolesRepository
from app.domains.roles.service import RolesService
from app.domains.support_access.repository import SupportAccessRepository
from tests.platform_access_helpers import create_test_platform_user


async def set_test_recent_confirmation(
    session: AsyncSession, *, user_id: UUID, session_id: UUID | None = None
) -> UUID:
    """Bind direct service fixtures to a real authenticated confirmation session."""
    now = datetime.now(UTC)
    auth_session = Session(
        id=session_id or uuid4(),
        user_id=user_id,
        refresh_token_hash=hash_token(f"role-confirmation-{uuid4()}"),
        expires_at=now + timedelta(hours=1),
        mfa_verified_at=now,
    )
    session.add(auth_session)
    await session.flush()
    await session.execute(
        text(
            "SELECT set_config('app.user_id', :user_id, true), "
            "set_config('app.auth_session_id', :session_id, true), "
            "set_config('app.mfa_verified_at', :verified_at, true), "
            "set_config('app.password_verified_at', '', true)"
        ),
        {
            "user_id": str(user_id),
            "session_id": str(auth_session.id),
            "verified_at": str(int(now.timestamp())),
        },
    )
    return auth_session.id


async def _get_publication_actor(session: AsyncSession):  # type: ignore[no-untyped-def]
    await session.execute(text("SELECT set_config('app.support_access_session_id', '', true)"))
    actor = session.info.get("published_role_actor")
    if actor is None:
        await session.execute(text("SELECT set_config('app.support_session', 'true', true)"))
        await session.execute(text("SELECT set_config('app.user_id', '', true)"))
        await session.execute(text("SELECT set_config('app.tenant_id', '', true)"))
        actor = await create_test_platform_user(
            session,
            access_kind="developer",
            email=f"published-role-actor-{uuid4().hex[:8]}@aurum.tj",
            full_name="Published role test actor",
        )
        session.info["published_role_actor"] = actor
    return actor


async def _activate_scoped_publication(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    actor_id: UUID,
    permission_codes: set[str],
) -> None:
    sessions = session.info.setdefault("published_role_support_sessions", {})
    session_context = sessions.get(tenant_id)
    if session_context is None:
        now = datetime.now(UTC)
        auth_session = Session(
            user_id=actor_id,
            refresh_token_hash=hash_token(f"published-role-{uuid4()}"),
            expires_at=now + timedelta(hours=1),
            mfa_verified_at=now,
        )
        session.add(auth_session)
        await session.flush()
        support_session = await SupportAccessRepository(session).create_session(
            tenant_id=tenant_id,
            actor_user_id=actor_id,
            actor_session_id=auth_session.id,
            reason="Published role test fixture",
            capabilities=sorted(
                {"roles.assign", "roles.create", "roles.update", *permission_codes}
            ),
            is_read_only=False,
            started_at=now,
            expires_at=now + timedelta(minutes=15),
        )
        support_session_id = support_session.id
        auth_session_id = auth_session.id
        sessions[tenant_id] = (support_session_id, auth_session_id)
    else:
        support_session_id, auth_session_id = session_context
        await session.execute(
            text("""
                INSERT INTO public.support_access_capability (
                  support_access_session_id,
                  tenant_id,
                  permission_code,
                  created_by
                )
                SELECT :session_id, :tenant_id, code, :actor_id
                FROM unnest(CAST(:capabilities AS TEXT[])) AS code
                ON CONFLICT DO NOTHING
                """),
            {
                "session_id": support_session_id,
                "tenant_id": tenant_id,
                "actor_id": actor_id,
                "capabilities": sorted(
                    {
                        "roles.assign",
                        "roles.create",
                        "roles.update",
                        *permission_codes,
                    }
                ),
            },
        )

    await session.execute(
        text("SELECT set_config('app.support_access_session_id', :session_id, true)"),
        {"session_id": str(support_session_id)},
    )
    await session.execute(
        text("SELECT set_config('app.auth_session_id', :session_id, true)"),
        {"session_id": str(auth_session_id)},
    )


async def provision_test_owner(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    email: str,
    full_name: str,
) -> tuple[AppUser, TenantMembership, TenantOwnership, Role]:
    """Provision an owner with the same authenticated support context as the API."""

    actor = await _get_publication_actor(session)
    now = datetime.now(UTC)
    auth_session = Session(
        user_id=actor.id,
        refresh_token_hash=hash_token(f"owner-provision-{uuid4()}"),
        expires_at=now + timedelta(hours=1),
        mfa_verified_at=now,
    )
    session.add(auth_session)
    await session.flush()
    await session.execute(
        text("SELECT set_config('app.auth_session_id', :session_id, true)"),
        {"session_id": str(auth_session.id)},
    )
    await session.execute(text("SELECT set_config('app.support_session', 'true', true)"))
    await session.execute(
        text("SELECT set_config('app.mfa_verified_at', :verified_at, true)"),
        {"verified_at": str(int(now.timestamp()))},
    )
    await session.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": str(actor.id)},
    )
    await session.execute(text("SELECT set_config('app.tenant_id', '', true)"))
    return await RolesService(RolesRepository(session)).provision_owner(
        tenant_id=tenant_id,
        email=email,
        full_name=full_name,
        actor_id=actor.id,
    )


async def create_published_test_role(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    name: str,
    permission_codes: Iterable[str],
    level: int = 4,
) -> Role:
    """Create arbitrary test role data through the guarded initial publication path."""

    session_user = str(await session.scalar(text("SELECT session_user")))
    actor = None
    codes = set(permission_codes)
    if session_user in {"aurum_app", "aurum_support"}:
        actor = await _get_publication_actor(session)

        await session.execute(text("SELECT set_config('app.support_session', 'true', true)"))
        await session.execute(
            text("SELECT set_config('app.mfa_verified_at', :verified_at, true)"),
            {"verified_at": str(int(datetime.now(UTC).timestamp()))},
        )
        await session.execute(
            text("SELECT set_config('app.user_id', :user_id, true)"),
            {"user_id": str(actor.id)},
        )
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )
        await _activate_scoped_publication(
            session,
            tenant_id=tenant_id,
            actor_id=actor.id,
            permission_codes=codes,
        )

    role = Role(
        tenant_id=tenant_id,
        name=name,
        level=level,
        is_system=False,
    )
    session.add(role)
    await session.flush()
    await session.refresh(role)
    for code in sorted(codes):
        session.add(RolePermission(role_id=role.id, permission_code=code))
    await session.flush()
    if session_user in {"aurum_app", "aurum_support"}:
        await RolesRepository(session).initialize_role_version(role.id)
    else:
        await session.execute(
            text("""
                INSERT INTO public.access_role_version (
                  id, role_id, tenant_id, version, name, description, status,
                  creation_xid, published_at, created_by
                )
                SELECT
                  gen_random_uuid(), role.id, role.tenant_id, role.version,
                  role.name, role.description, 'published', txid_current(),
                  statement_timestamp(), role.created_by
                FROM public.role AS role
                WHERE role.id = :role_id
                """),
            {"role_id": role.id},
        )
        await session.execute(
            text("""
                INSERT INTO public.access_role_version_permission (
                  role_version_id, permission_code
                )
                SELECT version.id, role_permission.permission_code
                FROM public.access_role_version AS version
                JOIN public.role_permission AS role_permission
                  ON role_permission.role_id = version.role_id
                WHERE version.role_id = :role_id
                  AND version.status = 'published'
                """),
            {"role_id": role.id},
        )
    return role
