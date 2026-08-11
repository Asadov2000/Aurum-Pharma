"""Platform team accounts are unprivileged until separately granted access."""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_password
from app.domains.platform_accounts import service as platform_accounts_service
from tests.auth_helpers import create_support_access_token
from tests.platform_access_helpers import create_test_platform_user


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


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

    activated = await platform_accounts_client.post(
        "/api/v1/auth/platform-activation",
        json={"token": activation_token, "password": "StrongPlatform9Password"},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json() == {"status": "activated"}

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
