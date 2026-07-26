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
from app.core.security import hash_token
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
    "reason_code": "platform_staff_onboarding",
    "reason": "Approved platform support onboarding request",
}


async def _target(db: AsyncSession) -> AppUser:
    user = AppUser(
        email=f"platform-target-{uuid4().hex}@example.invalid",
        full_name="Platform Target",
        status="active",
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def _session(db: AsyncSession, user: AppUser) -> Session:
    session = Session(
        user_id=user.id,
        refresh_token_hash=hash_token(secrets.token_hex(32)),
        expires_at=utc_now() + timedelta(days=1),
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
    target = await _target(db_session)
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
    target = await _target(db_session)
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
    target = await _target(db_session)
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

    administrator_response = await platform_client.get(
        "/api/v1/admin/platform-access/grants",
        headers=_headers(administrator_token),
    )
    stale_response = await platform_client.get(
        "/api/v1/admin/platform-access/grants",
        headers=_headers(stale_developer_token),
    )

    assert administrator_response.status_code == 403
    assert stale_response.status_code == 403
    assert stale_response.json()["error"]["details"]["reason"] == "mfa_step_up_required"


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
