"""Tenant administrators can end employee sessions without crossing boundaries."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.core.deps import _seed_request_db_context, get_db
from app.core.security import create_access_token, decode_access_token, hash_token
from app.core.time import utc_now
from app.domains.audit.models import AuditLog
from app.domains.auth.models import AppUser, Session
from app.domains.roles.models import TenantMembership
from app.domains.support_access.repository import SupportAccessRepository
from app.domains.support_access.service import SupportAccessService
from app.main import app
from tests.auth_helpers import create_support_access_token


async def _session(db: AsyncSession, user: AppUser) -> Session:
    auth_session = Session(
        user_id=user.id,
        refresh_token_hash=hash_token(str(uuid4())),
        expires_at=utc_now() + timedelta(days=1),
    )
    db.add(auth_session)
    await db.flush()
    await db.refresh(auth_session)
    return auth_session


async def _headers(db: AsyncSession, user: AppUser) -> dict[str, str]:
    auth_session = await _session(db, user)
    token = create_access_token(
        user.id,
        tenant_id=user.home_tenant_id,
        is_developer=False,
        is_administrator=False,
        session_id=auth_session.id,
    )
    return {"Authorization": f"Bearer {token}"}


async def test_owner_revokes_employee_sessions_through_the_api(
    client: AsyncClient,
    db_session: AsyncSession,
    make_tenant,
    make_owner,
    make_user,
) -> None:
    tenant = await make_tenant()
    owner, _membership, _ownership, _role = await make_owner(tenant_id=tenant.id)
    employee = await make_user(home_tenant_id=tenant.id)
    unprivileged = await make_user(home_tenant_id=tenant.id)
    protected_owner = await make_user(home_tenant_id=tenant.id, is_owner=True)
    other_tenant = await make_tenant()
    outsider = await make_user(home_tenant_id=other_tenant.id)
    first = await _session(db_session, employee)
    second = await _session(db_session, employee)
    outsider_session = await _session(db_session, outsider)
    first_id = first.id
    second_id = second.id
    outsider_session_id = outsider_session.id
    tenant_id = tenant.id
    owner_id = owner.id
    employee_id = employee.id

    async def _override(request: Request) -> AsyncIterator[AsyncSession]:
        await _seed_request_db_context(request, db_session)
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        denied = await client.post(
            f"/api/v1/users/{employee.id}/sessions/revoke",
            headers=await _headers(db_session, unprivileged),
        )
        assert denied.status_code == 403

        response = await client.post(
            f"/api/v1/users/{employee.id}/sessions/revoke",
            headers=await _headers(db_session, owner),
        )
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "revoked_count": 2}

        protected = await client.post(
            f"/api/v1/users/{protected_owner.id}/sessions/revoke",
            headers=await _headers(db_session, owner),
        )
        assert protected.status_code == 403

        cross_tenant = await client.post(
            f"/api/v1/users/{outsider.id}/sessions/revoke",
            headers=await _headers(db_session, owner),
        )
        assert cross_tenant.status_code == 404
    finally:
        app.dependency_overrides.pop(get_db, None)

    db_session.expire_all()
    sessions = (
        (
            await db_session.execute(
                select(Session).where(Session.id.in_([first_id, second_id, outsider_session_id]))
            )
        )
        .scalars()
        .all()
    )
    by_id = {session.id: session for session in sessions}
    assert by_id[first_id].revoked_reason == "tenant_admin_revoked"
    assert by_id[second_id].revoked_reason == "tenant_admin_revoked"
    assert by_id[outsider_session_id].revoked_at is None

    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.user_id == owner_id,
                AuditLog.table_name == "session",
                AuditLog.record_id == employee_id,
            )
        )
    ).scalar_one()
    assert audit.metadata_json == {
        "event": "tenant_user_sessions_revoked",
        "target_user_id": str(employee_id),
        "revoked_count": 2,
    }


async def test_scoped_administrator_revokes_employee_sessions_with_explicit_capability(
    client: AsyncClient,
    db_session: AsyncSession,
    make_tenant,
    make_user,
) -> None:
    tenant = await make_tenant()
    administrator = await make_user(
        email="session-security-admin@aurum.tj",
        is_administrator=True,
    )
    employee = await make_user(home_tenant_id=tenant.id)
    tenant_id = tenant.id
    employee_id = employee.id
    employee_session = await _session(db_session, employee)
    employee_session_id = employee_session.id
    await db_session.flush()

    token = await create_support_access_token(db_session, administrator)
    actor_session_id = UUID(str(decode_access_token(token)["sid"]))
    support_session = await SupportAccessService(SupportAccessRepository(db_session)).start_session(
        actor_user_id=administrator.id,
        actor_session_id=actor_session_id,
        actor_is_developer=False,
        actor_is_administrator=True,
        tenant_id=tenant_id,
        reason="End an employee session after a verified security request",
        duration_minutes=10,
        requested_capabilities=["users.block"],
    )

    async def _override(request: Request) -> AsyncIterator[AsyncSession]:
        request.state.support_access_resolved = True
        request.state.tenant_id = tenant_id
        request.state.is_support_session = True
        request.state.support_access_capabilities = support_session.capabilities
        request.state.support_access_reason = support_session.reason
        request.state.support_access_expires_at = support_session.expires_at
        request.state.support_access_tenant_name = support_session.tenant_name
        request.state.support_access_is_read_only = support_session.is_read_only
        await _seed_request_db_context(request, db_session)
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        response = await client.post(
            f"/api/v1/users/{employee_id}/sessions/revoke",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Aurum-Support-Session": str(support_session.id),
            },
        )
        blocked = await client.post(
            f"/api/v1/users/{employee_id}/block",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Aurum-Support-Session": str(support_session.id),
            },
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "revoked_count": 1}
    assert blocked.status_code == 200, blocked.text
    assert blocked.json() == {"status": "suspended"}
    db_session.expire_all()
    revoked_session = await db_session.get(Session, employee_session_id)
    assert revoked_session is not None
    assert revoked_session.revoked_reason == "tenant_admin_revoked"
    membership = (
        await db_session.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.user_id == employee_id,
            )
        )
    ).scalar_one()
    assert membership.status == "suspended"
