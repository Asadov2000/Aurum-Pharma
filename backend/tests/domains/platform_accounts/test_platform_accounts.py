"""Platform team accounts are unprivileged until separately granted access."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.security import (
    generate_code_salt,
    hash_code,
    verify_password,
)
from app.core.time import utc_now
from app.domains.auth.models import EmailCode, Session
from app.domains.auth.repository import AuthRepository
from app.domains.auth.service import AuthService
from app.domains.platform_accounts import service as platform_accounts_service
from tests.auth_helpers import create_support_access_token
from tests.platform_access_helpers import create_test_platform_user


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _action_payload(*, version: int, reason_code: str = "access_review") -> dict[str, object]:
    return {
        "version": version,
        "operation_id": str(uuid4()),
        "reason_code": reason_code,
        "reason": "Verified platform account lifecycle test action",
    }


async def _seed_login_code(
    db_session: AsyncSession,
    *,
    email: str,
    code: str = "123456",
) -> None:
    salt = generate_code_salt()
    db_session.add(
        EmailCode(
            email_lower=email.lower(),
            code_hash=hash_code(code, salt),
            code_salt=salt,
            purpose="login",
            ip_address="127.0.0.1",
            expires_at=utc_now() + timedelta(minutes=10),
        )
    )
    await db_session.flush()


async def test_developer_invites_and_candidate_activates_without_platform_access(
    db_session: AsyncSession,
    platform_accounts_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    developer = await create_test_platform_user(db_session, access_kind="developer")
    access_token = await create_support_access_token(db_session, developer)
    activation_token = "known-test-platform-activation-token-123456789"
    monkeypatch.setattr(
        platform_accounts_service.secrets,
        "token_urlsafe",
        lambda _size: activation_token,
    )
    email = f"candidate-{uuid4().hex}@example.com"

    invited = await platform_accounts_client.post(
        "/api/v1/admin/platform-accounts/invitations",
        json={"email": email, "full_name": "Platform Candidate"},
        headers=_headers(access_token),
    )

    assert invited.status_code == 201, invited.text
    invitation_body = invited.json()
    assert invitation_body["status"] == "invited"
    assert invitation_body["activation_token"] is None
    await db_session.execute(
        text(
            "SET CONSTRAINTS trg_platform_staff_profile_status_consistency, "
            "trg_platform_staff_user_status_consistency IMMEDIATE"
        )
    )
    await db_session.execute(text("SET CONSTRAINTS ALL DEFERRED"))

    activated = await platform_accounts_client.post(
        "/api/v1/auth/platform-activation",
        json={"token": activation_token, "password": "StrongPlatform9Password"},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json() == {"status": "activated"}
    await db_session.execute(
        text(
            "SET CONSTRAINTS trg_platform_staff_profile_status_consistency, "
            "trg_platform_staff_user_status_consistency IMMEDIATE"
        )
    )
    await db_session.execute(text("SET CONSTRAINTS ALL DEFERRED"))

    account = (
        (
            await db_session.execute(
                text("""
                    SELECT
                      account.status,
                      account.password_hash,
                      account.is_developer,
                      account.is_administrator,
                      account.home_tenant_id,
                      profile.status AS profile_status,
                      profile.invitation_token_hash,
                      EXISTS (
                        SELECT 1
                        FROM public.tenant_membership AS membership
                        WHERE membership.user_id = account.id
                      ) AS has_membership,
                      EXISTS (
                        SELECT 1
                        FROM public.platform_access_grant AS platform_grant
                        WHERE platform_grant.user_id = account.id
                          AND platform_grant.status IN ('pending', 'active')
                      ) AS has_platform_access
                    FROM public.app_user AS account
                    JOIN public.platform_staff_account AS profile
                      ON profile.user_id = account.id
                    WHERE account.email_lower = lower(:email)
                    """),
                {"email": email},
            )
        )
        .mappings()
        .one()
    )
    assert account["status"] == "active"
    assert account["profile_status"] == "active"
    assert verify_password("StrongPlatform9Password", account["password_hash"])
    assert account["invitation_token_hash"] is None
    assert account["home_tenant_id"] is None
    assert account["is_developer"] is False
    assert account["is_administrator"] is False
    assert account["has_membership"] is False
    assert account["has_platform_access"] is False

    replay = await platform_accounts_client.post(
        "/api/v1/auth/platform-activation",
        json={"token": activation_token, "password": "AnotherStrong9Password"},
    )
    assert replay.status_code == 401


async def test_administrator_with_explicit_capability_can_invite_but_cannot_grant_access(
    db_session: AsyncSession,
    platform_accounts_client: AsyncClient,
) -> None:
    administrator = await create_test_platform_user(
        db_session,
        access_kind="administrator",
    )
    access_token = await create_support_access_token(db_session, administrator)

    invited = await platform_accounts_client.post(
        "/api/v1/admin/platform-accounts/invitations",
        json={
            "email": f"admin-invited-{uuid4().hex}@example.com",
            "full_name": "Admin Invited Candidate",
        },
        headers=_headers(access_token),
    )
    assert invited.status_code == 201, invited.text

    grant_attempt = await platform_accounts_client.post(
        "/api/v1/admin/platform-access/grants",
        json={
            "user_id": invited.json()["user_id"],
            "access_kind": "administrator",
            "capabilities": ["platform.tenants.view"],
            "reason_code": "platform_staff_onboarding",
            "reason": "Administrator must not assign platform privileges",
        },
        headers=_headers(access_token),
    )
    assert grant_attempt.status_code == 403


async def test_unmanaged_active_account_is_not_eligible_for_platform_access(
    db_session: AsyncSession,
    platform_accounts_client: AsyncClient,
) -> None:
    developer = await create_test_platform_user(db_session, access_kind="developer")
    access_token = await create_support_access_token(db_session, developer)
    target_id = await db_session.scalar(
        text("""
            INSERT INTO public.app_user (
              email, full_name, password_hash, status, activated_at
            ) VALUES (
              :email,
              'Unmanaged Account',
              '$2b$12$invalid-but-present-password-hash',
              'active',
              statement_timestamp()
            )
            RETURNING id
            """),
        {"email": f"unmanaged-{uuid4().hex}@example.invalid"},
    )
    assert target_id is not None

    response = await platform_accounts_client.post(
        "/api/v1/admin/platform-access/grants",
        json={
            "user_id": str(target_id),
            "access_kind": "administrator",
            "capabilities": ["platform.tenants.view"],
            "reason_code": "platform_staff_onboarding",
            "reason": "Unmanaged account must not receive platform privileges",
        },
        headers=_headers(access_token),
    )
    assert response.status_code == 422


async def test_platform_account_list_requires_exact_capability_and_support_context(
    db_session: AsyncSession,
    platform_accounts_client: AsyncClient,
) -> None:
    developer = await create_test_platform_user(db_session, access_kind="developer")
    access_token = await create_support_access_token(db_session, developer)
    invited = await platform_accounts_client.post(
        "/api/v1/admin/platform-accounts/invitations",
        json={
            "email": f"listed-{uuid4().hex}@example.com",
            "full_name": "Listed Platform Candidate",
        },
        headers=_headers(access_token),
    )
    assert invited.status_code == 201, invited.text

    response = await platform_accounts_client.get(
        "/api/v1/admin/platform-accounts",
        headers=_headers(access_token),
    )

    assert response.status_code == 200, response.text
    assert response.json()["total"] >= 1
    assert all("invitation_token_hash" not in item for item in response.json()["items"])


async def test_reinvite_rotates_token_is_idempotent_and_blocks_normal_login(
    db_session: AsyncSession,
    platform_accounts_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    developer = await create_test_platform_user(db_session, access_kind="developer")
    access_token = await create_support_access_token(db_session, developer)
    original_token = "original-platform-activation-token-123456789"
    replacement_token = "replacement-platform-activation-token-123456"
    issued_tokens = iter((original_token, replacement_token, "unused-replay-token-123456789"))
    monkeypatch.setattr(
        platform_accounts_service.secrets,
        "token_urlsafe",
        lambda _size: next(issued_tokens),
    )
    email = f"reinvite-{uuid4().hex}@example.com"

    invited = await platform_accounts_client.post(
        "/api/v1/admin/platform-accounts/invitations",
        json={"email": email, "full_name": "Reinvited Candidate"},
        headers=_headers(access_token),
    )
    assert invited.status_code == 201, invited.text

    login_service = AuthService(AuthRepository(db_session))
    login_code = "123456"
    await _seed_login_code(db_session, email=email, code=login_code)
    with pytest.raises(NotFoundError):
        await login_service.verify_login_code(
            email=email,
            code=login_code,
            password=None,
            ip_address="127.0.0.1",
        )

    payload = _action_payload(version=invited.json()["version"], reason_code="invitation_delivery")
    reinvited = await platform_accounts_client.post(
        f"/api/v1/admin/platform-accounts/{invited.json()['user_id']}/reinvite",
        json=payload,
        headers=_headers(access_token),
    )
    assert reinvited.status_code == 200, reinvited.text
    assert reinvited.json()["version"] == 2

    replay = await platform_accounts_client.post(
        f"/api/v1/admin/platform-accounts/{invited.json()['user_id']}/reinvite",
        json=payload,
        headers=_headers(access_token),
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["version"] == 2
    assert replay.json()["activation_token"] is None

    old_activation = await platform_accounts_client.post(
        "/api/v1/auth/platform-activation",
        json={"token": original_token, "password": "StrongPlatform9Password"},
    )
    assert old_activation.status_code == 401
    activated = await platform_accounts_client.post(
        "/api/v1/auth/platform-activation",
        json={"token": replacement_token, "password": "StrongPlatform9Password"},
    )
    assert activated.status_code == 200, activated.text

    events = int(
        await db_session.scalar(
            text("""
                SELECT count(*) FROM public.platform_staff_account_event
                WHERE user_id = :user_id AND event_type = 'reinvited'
                """),
            {"user_id": UUID(invited.json()["user_id"])},
        )
        or 0
    )
    assert events == 1


async def test_block_unblock_and_offboard_revoke_sessions_without_restoring_access(
    db_session: AsyncSession,
    platform_accounts_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    developer = await create_test_platform_user(db_session, access_kind="developer")
    access_token = await create_support_access_token(db_session, developer)
    activation_token = "lifecycle-platform-activation-token-123456789"
    monkeypatch.setattr(
        platform_accounts_service.secrets,
        "token_urlsafe",
        lambda _size: activation_token,
    )
    email = f"lifecycle-{uuid4().hex}@example.com"
    invited = await platform_accounts_client.post(
        "/api/v1/admin/platform-accounts/invitations",
        json={"email": email, "full_name": "Lifecycle Candidate"},
        headers=_headers(access_token),
    )
    assert invited.status_code == 201, invited.text
    activated = await platform_accounts_client.post(
        "/api/v1/auth/platform-activation",
        json={"token": activation_token, "password": "StrongPlatform9Password"},
    )
    assert activated.status_code == 200, activated.text
    target_id = UUID(invited.json()["user_id"])

    target_session = Session(
        user_id=target_id,
        refresh_token_hash=uuid4().hex + uuid4().hex,
        expires_at=utc_now() + timedelta(days=1),
    )
    db_session.add(target_session)
    await db_session.flush()

    current_version = int(
        await db_session.scalar(
            text("SELECT version FROM public.platform_staff_account WHERE user_id = :user_id"),
            {"user_id": target_id},
        )
    )
    block_payload = _action_payload(version=current_version, reason_code="security_incident")
    blocked = await platform_accounts_client.post(
        f"/api/v1/admin/platform-accounts/{target_id}/block",
        json=block_payload,
        headers=_headers(access_token),
    )
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["status"] == "blocked"
    assert (
        int(
            await db_session.scalar(
                text("""
                SELECT count(*) FROM public.session
                WHERE user_id = :user_id AND revoked_at IS NULL
                """),
                {"user_id": target_id},
            )
            or 0
        )
        == 0
    )

    block_replay = await platform_accounts_client.post(
        f"/api/v1/admin/platform-accounts/{target_id}/block",
        json=block_payload,
        headers=_headers(access_token),
    )
    assert block_replay.status_code == 200, block_replay.text
    assert block_replay.json()["version"] == blocked.json()["version"]

    unblocked = await platform_accounts_client.post(
        f"/api/v1/admin/platform-accounts/{target_id}/unblock",
        json=_action_payload(version=blocked.json()["version"]),
        headers=_headers(access_token),
    )
    assert unblocked.status_code == 200, unblocked.text
    assert unblocked.json()["status"] == "active"
    assert (
        int(
            await db_session.scalar(
                text(
                    "SELECT count(*) FROM public.session "
                    "WHERE user_id = :user_id AND revoked_at IS NULL"
                ),
                {"user_id": target_id},
            )
            or 0
        )
        == 0
    )

    stale = await platform_accounts_client.post(
        f"/api/v1/admin/platform-accounts/{target_id}/offboard",
        json=_action_payload(version=blocked.json()["version"]),
        headers=_headers(access_token),
    )
    assert stale.status_code == 409

    offboarded = await platform_accounts_client.post(
        f"/api/v1/admin/platform-accounts/{target_id}/offboard",
        json=_action_payload(
            version=unblocked.json()["version"],
            reason_code="employment_ended",
        ),
        headers=_headers(access_token),
    )
    assert offboarded.status_code == 200, offboarded.text
    assert offboarded.json()["status"] == "offboarded"
    account_status = await db_session.scalar(
        text("SELECT status FROM public.app_user WHERE id = :user_id"),
        {"user_id": target_id},
    )
    assert account_status == "archived"

    cannot_restore = await platform_accounts_client.post(
        f"/api/v1/admin/platform-accounts/{target_id}/unblock",
        json=_action_payload(version=offboarded.json()["version"]),
        headers=_headers(access_token),
    )
    assert cannot_restore.status_code == 409


async def test_platform_lifecycle_requires_recent_mfa(
    db_session: AsyncSession,
    platform_accounts_client: AsyncClient,
) -> None:
    developer = await create_test_platform_user(db_session, access_kind="developer")
    stale_token = await create_support_access_token(
        db_session,
        developer,
        mfa_verified_at=utc_now() - timedelta(minutes=20),
    )

    response = await platform_accounts_client.post(
        f"/api/v1/admin/platform-accounts/{uuid4()}/block",
        json=_action_payload(version=1),
        headers=_headers(stale_token),
    )

    assert response.status_code == 403
    assert response.json()["error"]["details"] == {"reason": "mfa_step_up_required"}
