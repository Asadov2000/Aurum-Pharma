"""Protected platform access is explicit, approved, isolated, and revocable."""

from __future__ import annotations

import asyncio
import secrets
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import hash_password, hash_token
from app.core.time import utc_now
from app.domains.audit.models import AuditLog
from app.domains.auth.models import AppUser, PlatformAccessGrant, Session
from app.domains.foundation.models import Tenant
from app.domains.platform_access import service as platform_access_service
from app.domains.roles.models import TenantMembership
from tests.auth_helpers import create_support_access_token
from tests.platform_access_helpers import (
    create_test_platform_user,
    make_test_developer_sole,
)

REQUEST_PAYLOAD = {
    "access_kind": "administrator",
    "capabilities": [
        "platform.tenants.view",
        "platform.support.use",
    ],
    "reason_code": "platform_staff_onboarding",
    "reason": "Approved platform support onboarding request",
}


async def _target(db: AsyncSession, actor: AppUser) -> AppUser:
    actor_session = await _session(db, actor)
    token_hash = hash_token(secrets.token_urlsafe(32))
    await db.execute(
        text("SELECT set_config('app.user_id', :value, true)"),
        {"value": str(actor.id)},
    )
    await db.execute(
        text("SELECT set_config('app.auth_session_id', :value, true)"),
        {"value": str(actor_session.id)},
    )
    user_id = await db.scalar(
        text("""
            SELECT invitation.user_id
            FROM public.create_platform_staff_invitation(
              :actor_user_id,
              :actor_session_id,
              :email,
              'Platform Target',
              :token_hash,
              statement_timestamp() + INTERVAL '1 hour'
            ) AS invitation
            """),
        {
            "actor_user_id": actor.id,
            "actor_session_id": actor_session.id,
            "email": f"platform-target-{uuid4().hex}@example.invalid",
            "token_hash": token_hash,
        },
    )
    assert user_id is not None
    activated_id = await db.scalar(
        text("SELECT public.accept_platform_staff_invitation(:token_hash, :password_hash)"),
        {
            "token_hash": token_hash,
            "password_hash": hash_password("PlatformTest9Password"),
        },
    )
    assert activated_id == user_id
    user = await db.get(AppUser, user_id)
    assert user is not None
    return user


async def _session(db: AsyncSession, user: AppUser) -> Session:
    session = Session(
        user_id=user.id,
        refresh_token_hash=hash_token(secrets.token_hex(32)),
        expires_at=utc_now() + timedelta(days=1),
        mfa_verified_at=utc_now(),
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)
    return session


def _headers(token: str, *, request_id: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if request_id is not None:
        headers["X-Request-ID"] = request_id
    return headers


async def test_grant_activation_revokes_target_sessions_and_redacts_audit_reasons(
    db_session: AsyncSession,
    platform_client: AsyncClient,
) -> None:
    developer = await create_test_platform_user(
        db_session,
        access_kind="developer",
        full_name="Sole Developer",
    )
    await make_test_developer_sole(db_session, developer)
    target = await _target(db_session, developer)
    target_session = await _session(db_session, target)
    token = await create_support_access_token(db_session, developer)
    request_id = "platform-grant-test"

    response = await platform_client.post(
        "/api/v1/admin/platform-access/grants",
        json={"user_id": str(target.id), **REQUEST_PAYLOAD},
        headers=_headers(token, request_id=request_id),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "active"
    assert body["requires_approval"] is False
    assert body["capabilities"] == sorted(REQUEST_PAYLOAD["capabilities"])
    assert body["request_reason_code"] == "platform_staff_onboarding"
    await db_session.refresh(target)
    await db_session.refresh(target_session)
    assert target.is_administrator is True
    assert target_session.revoked_at is not None
    assert target_session.revoked_reason == "platform_access_changed"

    audit = (
        await db_session.scalars(
            select(AuditLog).where(
                AuditLog.table_name == "platform_access_grant",
                AuditLog.record_id == UUID(body["id"]),
                AuditLog.action == "INSERT",
            )
        )
    ).one()
    assert audit.metadata_json is not None
    assert audit.metadata_json["request_id"] == request_id
    assert audit.metadata_json["request_reason_code"] == "platform_staff_onboarding"
    assert "request_reason" not in audit.metadata_json
    assert "approval_reason" not in audit.metadata_json
    assert "revoke_reason" not in audit.metadata_json


async def test_capability_catalog_and_database_exclude_developer_only_admin_access(
    db_session: AsyncSession,
    platform_client: AsyncClient,
) -> None:
    developer = await create_test_platform_user(
        db_session,
        access_kind="developer",
    )
    administrator = await create_test_platform_user(
        db_session,
        access_kind="administrator",
    )
    token = await create_support_access_token(db_session, developer)

    catalog_response = await platform_client.get(
        "/api/v1/admin/platform-access/capabilities",
        params={"access_kind": "administrator"},
        headers=_headers(token),
    )
    assert catalog_response.status_code == 200, catalog_response.text
    codes = {item["code"] for item in catalog_response.json()}
    assert "platform.sync.view" in codes
    assert "platform.sync.manage" not in codes
    assert "platform.access.manage" not in codes
    assert "platform.audit.global.view" not in codes

    grant_id = await db_session.scalar(
        select(PlatformAccessGrant.id).where(
            PlatformAccessGrant.user_id == administrator.id,
            PlatformAccessGrant.status == "active",
        )
    )
    assert grant_id is not None
    await db_session.execute(text("SELECT set_config('app.support_session', 'true', true)"))
    await db_session.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": str(developer.id)},
    )
    with pytest.raises(DBAPIError) as invalid_capability:
        async with db_session.begin_nested():
            await db_session.execute(
                text("""
                    INSERT INTO public.platform_access_grant_permission (
                      grant_id,
                      permission_code,
                      created_by
                    ) VALUES (
                      :grant_id,
                      'platform.sync.manage',
                      :developer_id
                    )
                    """),
                {"grant_id": grant_id, "developer_id": developer.id},
            )
    assert getattr(invalid_capability.value.orig, "sqlstate", None) == "42501"


async def test_second_developer_must_independently_approve(
    db_session: AsyncSession,
    platform_client: AsyncClient,
) -> None:
    requester = await create_test_platform_user(
        db_session,
        access_kind="developer",
        full_name="Requesting Developer",
    )
    approver = await create_test_platform_user(
        db_session,
        access_kind="developer",
        full_name="Approving Developer",
    )
    target = await _target(db_session, requester)
    target_session = await _session(db_session, target)
    requester_token = await create_support_access_token(db_session, requester)
    approver_token = await create_support_access_token(db_session, approver)

    requested = await platform_client.post(
        "/api/v1/admin/platform-access/grants",
        json={"user_id": str(target.id), **REQUEST_PAYLOAD},
        headers=_headers(requester_token),
    )
    assert requested.status_code == 201, requested.text
    pending = requested.json()
    assert pending["status"] == "pending"
    assert pending["requires_approval"] is True
    assert pending["approval_expires_at"] is not None

    self_approval = await platform_client.post(
        f"/api/v1/admin/platform-access/grants/{pending['id']}/approve",
        json={
            "version": pending["version"],
            "reason_code": "access_review",
            "reason": "Independent access review completed",
        },
        headers=_headers(requester_token),
    )
    assert self_approval.status_code == 403

    approved = await platform_client.post(
        f"/api/v1/admin/platform-access/grants/{pending['id']}/approve",
        json={
            "version": pending["version"],
            "reason_code": "access_review",
            "reason": "Independent access review completed",
        },
        headers=_headers(approver_token),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "active"
    assert approved.json()["approved_by"] == str(approver.id)
    await db_session.refresh(target_session)
    assert target_session.revoked_at is not None


async def test_approval_rechecks_current_delegation_envelope(
    db_session: AsyncSession,
    platform_client: AsyncClient,
) -> None:
    requester = await create_test_platform_user(
        db_session,
        access_kind="developer",
        full_name="Requesting Developer",
    )
    approver = await create_test_platform_user(
        db_session,
        access_kind="developer",
        full_name="Approving Developer",
    )
    target = await _target(db_session, requester)
    requester_token = await create_support_access_token(db_session, requester)
    approver_token = await create_support_access_token(db_session, approver)

    requested = await platform_client.post(
        "/api/v1/admin/platform-access/grants",
        json={"user_id": str(target.id), **REQUEST_PAYLOAD},
        headers=_headers(requester_token),
    )
    assert requested.status_code == 201, requested.text
    pending = requested.json()
    assert pending["status"] == "pending"

    await db_session.execute(
        text("""
            UPDATE public.permission
            SET administrator_grantable = false
            WHERE code = 'platform.support.use'
            """),
    )

    rejected = await platform_client.post(
        f"/api/v1/admin/platform-access/grants/{pending['id']}/approve",
        json={
            "version": pending["version"],
            "reason_code": "access_review",
            "reason": "Independent access review completed",
        },
        headers=_headers(approver_token),
    )

    assert rejected.status_code == 403, rejected.text
    await db_session.refresh(target)
    assert target.is_administrator is False


async def test_expired_approval_is_closed_atomically(
    db_session: AsyncSession,
    platform_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requester = await create_test_platform_user(
        db_session,
        access_kind="developer",
        full_name="Expiry Requesting Developer",
    )
    approver = await create_test_platform_user(
        db_session,
        access_kind="developer",
        full_name="Expiry Approving Developer",
    )
    target = await _target(db_session, requester)
    target_session = await _session(db_session, target)
    requester_token = await create_support_access_token(db_session, requester)
    approver_token = await create_support_access_token(db_session, approver)
    monkeypatch.setattr(
        platform_access_service,
        "APPROVAL_WINDOW",
        timedelta(seconds=3),
    )

    requested = await platform_client.post(
        "/api/v1/admin/platform-access/grants",
        json={"user_id": str(target.id), **REQUEST_PAYLOAD},
        headers=_headers(requester_token),
    )
    assert requested.status_code == 201, requested.text
    pending = requested.json()
    assert pending["status"] == "pending"

    await asyncio.sleep(3.5)
    expired = await platform_client.post(
        f"/api/v1/admin/platform-access/grants/{pending['id']}/approve",
        json={
            "version": pending["version"],
            "reason_code": "access_review",
            "reason": "Independent access review completed",
        },
        headers=_headers(approver_token),
    )

    assert expired.status_code == 200, expired.text
    assert expired.json()["status"] == "expired"
    assert expired.json()["revoke_reason_code"] == "approval_window_expired"
    await db_session.refresh(target)
    await db_session.refresh(target_session)
    assert target.is_administrator is False
    assert target_session.revoked_at is None


async def test_administrator_and_stale_mfa_cannot_read_grants(
    db_session: AsyncSession,
    platform_client: AsyncClient,
) -> None:
    developer = await create_test_platform_user(
        db_session,
        access_kind="developer",
        full_name="Access Review Developer",
    )
    administrator = await create_test_platform_user(
        db_session,
        access_kind="administrator",
    )
    administrator_token = await create_support_access_token(db_session, administrator)
    stale_at = utc_now() - timedelta(minutes=get_settings().MFA_STEP_UP_MINUTES + 1)
    stale_developer_token = await create_support_access_token(
        db_session,
        developer,
        mfa_verified_at=stale_at,
    )
    fresh_developer_token = await create_support_access_token(db_session, developer)

    administrator_response = await platform_client.get(
        "/api/v1/admin/platform-access/grants",
        headers=_headers(administrator_token),
    )
    stale_response = await platform_client.get(
        "/api/v1/admin/platform-access/grants",
        headers=_headers(stale_developer_token),
    )
    list_response = await platform_client.get(
        "/api/v1/admin/platform-access/grants",
        params={"user_id": str(developer.id)},
        headers=_headers(fresh_developer_token),
    )

    assert administrator_response.status_code == 403
    assert stale_response.status_code == 403
    assert stale_response.json()["error"]["details"]["reason"] == "mfa_step_up_required"
    assert list_response.status_code == 200, list_response.text
    listed_grant = list_response.json()["items"][0]
    assert listed_grant["user_id"] == str(developer.id)
    assert listed_grant["user_email"] == developer.email
    assert listed_grant["user_full_name"] == "Access Review Developer"


async def test_administrator_can_view_but_cannot_manage_edge_sync(
    db_session: AsyncSession,
    platform_client: AsyncClient,
) -> None:
    administrator = await create_test_platform_user(
        db_session,
        access_kind="administrator",
    )
    token = await create_support_access_token(db_session, administrator)

    view_response = await platform_client.get(
        "/api/v1/admin/sync/nodes",
        headers=_headers(token),
    )
    manage_response = await platform_client.post(
        "/api/v1/admin/sync/nodes",
        json={
            "tenant_id": str(uuid4()),
            "branch_id": str(uuid4()),
            "display_name": "Forbidden administrator node",
        },
        headers=_headers(token),
    )

    assert view_response.status_code == 200, view_response.text
    assert manage_response.status_code == 403
    assert "platform.sync.manage" in manage_response.json()["error"]["message"]


async def test_tenant_status_change_requires_billing_capability(
    db_session: AsyncSession,
    platform_client: AsyncClient,
) -> None:
    developer = await create_test_platform_user(
        db_session,
        access_kind="developer",
    )
    await make_test_developer_sole(db_session, developer)
    target = await _target(db_session, developer)
    developer_token = await create_support_access_token(db_session, developer)
    grant_response = await platform_client.post(
        "/api/v1/admin/platform-access/grants",
        json={
            "user_id": str(target.id),
            "access_kind": "administrator",
            "capabilities": ["platform.tenants.manage"],
            "reason_code": "platform_staff_onboarding",
            "reason": "Restricted tenant lifecycle administrator",
        },
        headers=_headers(developer_token),
    )
    assert grant_response.status_code == 201, grant_response.text
    await db_session.refresh(target)
    administrator_token = await create_support_access_token(db_session, target)
    tenant = Tenant(
        name=f"Restricted tenant {uuid4().hex}",
        contact_email=f"restricted-{uuid4().hex}@example.invalid",
    )
    db_session.add(tenant)
    await db_session.flush()

    ordinary_update = await platform_client.patch(
        f"/api/v1/admin/tenants/{tenant.id}",
        json={"name": "Restricted tenant renamed"},
        headers=_headers(administrator_token),
    )
    status_update = await platform_client.patch(
        f"/api/v1/admin/tenants/{tenant.id}",
        json={"status": "readonly"},
        headers=_headers(administrator_token),
    )

    assert ordinary_update.status_code == 200, ordinary_update.text
    assert status_update.status_code == 403
    assert "platform.billing.manage" in status_update.json()["error"]["message"]


async def test_revocation_is_versioned_and_immediately_invalidates_access(
    db_session: AsyncSession,
    platform_client: AsyncClient,
) -> None:
    developer = await create_test_platform_user(
        db_session,
        access_kind="developer",
    )
    administrator = await create_test_platform_user(
        db_session,
        access_kind="administrator",
    )
    administrator_token = await create_support_access_token(db_session, administrator)
    developer_token = await create_support_access_token(db_session, developer)
    grant = (
        await db_session.scalars(
            select(PlatformAccessGrant).where(
                PlatformAccessGrant.user_id == administrator.id,
                PlatformAccessGrant.status == "active",
            )
        )
    ).one()

    stale = await platform_client.post(
        f"/api/v1/admin/platform-access/grants/{grant.id}/revoke",
        json={
            "version": grant.version + 1,
            "reason_code": "responsibility_change",
            "reason": "Platform responsibilities have changed",
        },
        headers=_headers(developer_token),
    )
    assert stale.status_code == 409

    revoked = await platform_client.post(
        f"/api/v1/admin/platform-access/grants/{grant.id}/revoke",
        json={
            "version": grant.version,
            "reason_code": "responsibility_change",
            "reason": "Platform responsibilities have changed",
        },
        headers=_headers(developer_token),
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "revoked"
    await db_session.refresh(administrator)
    assert administrator.is_administrator is False

    rejected_session = await platform_client.get(
        "/api/v1/admin/platform-access/grants",
        headers=_headers(administrator_token),
    )
    assert rejected_session.status_code == 401


async def test_database_blocks_self_action_and_platform_tenant_mixing(
    db_session: AsyncSession,
    platform_client: AsyncClient,
) -> None:
    developer = await create_test_platform_user(
        db_session,
        access_kind="developer",
    )
    token = await create_support_access_token(db_session, developer)
    grant = (
        await db_session.scalars(
            select(PlatformAccessGrant).where(
                PlatformAccessGrant.user_id == developer.id,
                PlatformAccessGrant.status == "active",
            )
        )
    ).one()

    self_revoke = await platform_client.post(
        f"/api/v1/admin/platform-access/grants/{grant.id}/revoke",
        json={
            "version": grant.version,
            "reason_code": "security_incident",
            "reason": "Attempted self revocation must fail",
        },
        headers=_headers(token),
    )
    assert self_revoke.status_code == 403

    tenant = Tenant(
        name=f"Platform isolation {uuid4().hex}",
        contact_email=f"isolation-{uuid4().hex}@example.invalid",
    )
    db_session.add(tenant)
    await db_session.flush()

    with pytest.raises(DBAPIError) as membership_error:
        async with db_session.begin_nested():
            db_session.add(
                TenantMembership(
                    tenant_id=tenant.id,
                    user_id=developer.id,
                    full_name=developer.full_name,
                    status="active",
                )
            )
            await db_session.flush()
    assert getattr(membership_error.value.orig, "sqlstate", None) == "42501"

    with pytest.raises(DBAPIError) as home_tenant_error:
        async with db_session.begin_nested():
            await db_session.execute(
                text("""
                    UPDATE public.app_user
                    SET home_tenant_id = :tenant_id
                    WHERE id = :user_id
                    """),
                {"tenant_id": tenant.id, "user_id": developer.id},
            )
    assert getattr(home_tenant_error.value.orig, "sqlstate", None) == "42501"


async def test_only_independent_developer_can_deactivate_platform_account(
    db_session: AsyncSession,
) -> None:
    developer = await create_test_platform_user(
        db_session,
        access_kind="developer",
    )
    administrator = await create_test_platform_user(
        db_session,
        access_kind="administrator",
    )

    await db_session.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": str(administrator.id)},
    )
    with pytest.raises(DBAPIError) as self_disable:
        async with db_session.begin_nested():
            await db_session.execute(
                text("""
                    UPDATE public.app_user
                    SET status = 'archived'
                    WHERE id = :user_id
                    """),
                {"user_id": administrator.id},
            )
    assert getattr(self_disable.value.orig, "sqlstate", None) == "42501"

    await db_session.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": str(developer.id)},
    )
    await db_session.execute(
        text("""
            UPDATE public.app_user
            SET status = 'invited'
            WHERE id = :user_id
            """),
        {"user_id": administrator.id},
    )
    await db_session.refresh(administrator)
    assert administrator.status == "invited"
    status = await db_session.scalar(
        select(PlatformAccessGrant.status).where(PlatformAccessGrant.user_id == administrator.id)
    )
    assert status == "revoked"
