"""Demo opt-in changes login friction without granting recent-MFA authority."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_recent_account_mfa
from app.core.errors import PermissionDeniedError
from app.core.security import decode_access_token, hash_password
from app.core.time import utc_now
from app.domains.auth.models import AppUser, Session
from app.domains.auth.repository import AuthRepository
from app.domains.auth.service import AuthService, settings
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from tests.domains.auth.test_login import _seed_code
from tests.domains.auth.test_mfa import _totp_code
from tests.platform_access_helpers import create_test_platform_user
from tests.role_version_helpers import provision_test_owner

_PASSWORD = "Demo-Owner-Regression-Password-42"


async def _owner(db_session: AsyncSession, *, email: str = "owner@aurum.tj") -> AppUser:
    tenant = await FoundationService(FoundationRepository(db_session)).create_tenant(
        payload={"name": "Local demo login regression", "contact_email": "demo-test@aurum.tj"}
    )
    owner, _membership, _ownership, _role = await provision_test_owner(
        db_session,
        tenant_id=tenant.id,
        email=email,
        full_name="Demo regression owner",
    )
    owner.password_hash = hash_password(_PASSWORD)
    await db_session.flush()
    return owner


async def _login(client: AsyncClient, user: AppUser) -> str:
    response = await client.post(
        "/api/v1/auth/login/verify",
        json={"email": user.email, "code": "123456", "password": _PASSWORD},
    )
    assert response.status_code == 200
    assert "access_token" in response.json()
    assert client.cookies.get("aurum_refresh_token")
    return str(response.json()["access_token"])


async def _enroll_owner(client: AsyncClient, db_session: AsyncSession, owner: AppUser) -> None:
    await _seed_code(db_session, owner.email)
    login = await client.post(
        "/api/v1/auth/login/verify",
        json={"email": owner.email, "code": "123456", "password": _PASSWORD},
    )
    assert login.status_code == 200
    challenge = str(login.json()["challenge_token"])
    setup = await client.post("/api/v1/auth/mfa/enroll/start", json={"challenge_token": challenge})
    assert setup.status_code == 200
    confirmation = await client.post(
        "/api/v1/auth/mfa/enroll/confirm",
        json={
            "challenge_token": challenge,
            "code": _totp_code(str(setup.json()["secret"]), utc_now()),
        },
    )
    assert confirmation.status_code == 200
    client.cookies.clear()


@pytest.mark.parametrize("enrolled", [False, True])
async def test_local_demo_owner_can_login_and_refresh_without_mfa_authority(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    enrolled: bool,
) -> None:
    monkeypatch.setattr(settings, "AUTH_LOCAL_DEMO_OWNER_LOGIN", False)
    owner = await _owner(db_session)
    if enrolled:
        await _enroll_owner(auth_client, db_session, owner)
    monkeypatch.setattr(settings, "AUTH_LOCAL_DEMO_OWNER_LOGIN", True)
    await _seed_code(db_session, owner.email)
    access_token = await _login(auth_client, owner)
    claims = decode_access_token(access_token)
    assert claims["tenant_id"] == str(owner.home_tenant_id)
    assert claims.get("mfa_at") is None
    session = await db_session.get(Session, UUID(str(claims["sid"])))
    assert session is not None
    assert session.mfa_verified_at is None

    me = await auth_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"}
    )
    assert me.status_code == 200
    assert me.json()["is_tenant_owner"] is True

    refreshed = await auth_client.post(
        "/api/v1/auth/refresh", json={}, headers={"Origin": "http://localhost:5173"}
    )
    assert refreshed.status_code == 200
    refreshed_claims = decode_access_token(str(refreshed.json()["access_token"]))
    assert refreshed_claims.get("mfa_at") is None
    assert refreshed_claims["sid"] != claims["sid"]
    assert refreshed_claims["tenant_id"] == claims["tenant_id"]
    new_session = await db_session.get(Session, UUID(str(refreshed_claims["sid"])))
    assert new_session is not None
    assert new_session.mfa_verified_at is None

    with pytest.raises(PermissionDeniedError, match="Recent MFA"):
        await require_recent_account_mfa(
            CurrentUser(
                user_id=owner.id,
                tenant_id=owner.home_tenant_id,
                is_developer=False,
                is_administrator=False,
                session_id=new_session.id,
                mfa_verified_at=new_session.mfa_verified_at,
                is_tenant_owner=True,
            )
        )

    # Refresh lookups deliberately redact the password hash. Exercise this real
    # record, including account states that cannot be created for a last owner.
    identity = await AuthRepository(db_session).get_user_by_id(owner.id, session_id=new_session.id)
    assert identity is not None
    assert identity.password_hash is None
    if enrolled:
        assert identity.mfa_status == "active"
    assert AuthService._uses_local_demo_owner_login(identity)
    for status in ("invited", "blocked", "archived"):
        assert not AuthService._uses_local_demo_owner_login(replace(identity, status=status))
    for membership_status in (None, "pending", "suspended", "offboarded"):
        assert not AuthService._uses_local_demo_owner_login(
            replace(identity, membership_status=membership_status)
        )
    assert not AuthService._uses_local_demo_owner_login(replace(identity, home_tenant_id=None))

    # Turning off the exception must also reject sessions created while it was on.
    monkeypatch.setattr(settings, "AUTH_LOCAL_DEMO_OWNER_LOGIN", False)
    rejected = await auth_client.post(
        "/api/v1/auth/refresh", json={}, headers={"Origin": "http://localhost:5173"}
    )
    assert rejected.status_code == 401
    assert "access_token" not in rejected.json()


@pytest.mark.parametrize(
    ("enabled", "email"), [(False, "owner@aurum.tj"), (True, "other-owner@aurum.tj")]
)
async def test_other_owners_and_default_configuration_still_require_mfa(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    email: str,
) -> None:
    monkeypatch.setattr(settings, "AUTH_LOCAL_DEMO_OWNER_LOGIN", enabled)
    owner = await _owner(db_session, email=email)
    await _seed_code(db_session, owner.email)
    response = await auth_client.post(
        "/api/v1/auth/login/verify",
        json={"email": owner.email, "code": "123456", "password": _PASSWORD},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "mfa_enrollment_required"
    assert "access_token" not in response.json()
    assert auth_client.cookies.get("aurum_refresh_token") is None


@pytest.mark.parametrize(
    ("code", "password", "stored_password"),
    [
        ("999999", _PASSWORD, True),
        ("123456", "wrong-password", True),
        ("123456", None, True),
        ("123456", _PASSWORD, False),
        ("123456", None, False),
    ],
)
async def test_demo_owner_still_requires_valid_email_code_and_real_password(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    password: str | None,
    stored_password: bool,
) -> None:
    monkeypatch.setattr(settings, "AUTH_LOCAL_DEMO_OWNER_LOGIN", True)
    owner = await _owner(db_session)
    if not stored_password:
        owner.password_hash = None
        await db_session.flush()
    await _seed_code(db_session, owner.email)
    response = await auth_client.post(
        "/api/v1/auth/login/verify",
        json={"email": owner.email, "code": code, "password": password},
    )
    assert response.status_code == 401
    assert "access_token" not in response.json()
    assert auth_client.cookies.get("aurum_refresh_token") is None
    assert await db_session.scalar(select(Session.id).where(Session.user_id == owner.id)) is None


@pytest.mark.parametrize("access_kind", ["developer", "administrator"])
async def test_demo_email_never_exempts_platform_accounts(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    access_kind: str,
) -> None:
    monkeypatch.setattr(settings, "AUTH_LOCAL_DEMO_OWNER_LOGIN", True)
    user = await create_test_platform_user(
        db_session,
        access_kind=access_kind,
        email="owner@aurum.tj",
        full_name="Platform regression user",
        password_hash=hash_password(_PASSWORD),
    )
    await _seed_code(db_session, user.email)
    response = await auth_client.post(
        "/api/v1/auth/login/verify",
        json={"email": user.email, "code": "123456", "password": _PASSWORD},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "mfa_enrollment_required"
    assert "access_token" not in response.json()
