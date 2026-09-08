"""Access-token rollout must preserve real refresh sessions and tenant identity."""

from __future__ import annotations

from time import time
from uuid import UUID, uuid4

import jwt
import pytest
import pytest_asyncio
from httpx import AsyncClient, Cookies
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token, settings
from app.domains.auth.models import AppUser
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.roles.models import TenantMembership
from tests.domains.auth.test_login import _seed_code
from tests.domains.auth.test_optional_mfa import _actor

_BASE = "/api/v1/auth"
_ORIGIN = {"Origin": "http://localhost:5173"}


def _legacy_token(access: str) -> str:
    claims = jwt.decode(
        access,
        settings.JWT_SECRET,
        algorithms=[settings.JWT_ALGORITHM],
        issuer=settings.JWT_ISSUER,
        audience=settings.JWT_AUDIENCE,
    )
    for name in ("iss", "aud", "jti"):
        claims.pop(name)
    return jwt.encode(
        claims,
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
        headers={"typ": "JWT"},
    )


def _assert_strict_token(access: str, user: AppUser) -> str:
    assert jwt.get_unverified_header(access)["typ"] == "aurum-access+jwt"
    claims = decode_access_token(access)
    assert claims["iss"] == settings.JWT_ISSUER
    assert claims["aud"] == settings.JWT_AUDIENCE
    assert str(UUID(claims["jti"])) == claims["jti"]
    assert claims["sub"] == str(user.id)
    assert claims["tenant_id"] == str(user.home_tenant_id)
    return str(claims["jti"])


@pytest_asyncio.fixture
async def token_session(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,
) -> tuple[AppUser, str]:
    tenant = await FoundationService(FoundationRepository(db_session)).create_tenant(
        payload={
            "name": "Token contract tenant",
            "contact_email": f"token-contract-{uuid4()}@example.invalid",
        }
    )
    user = await make_user(home_tenant_id=tenant.id)
    db_session.add(
        TenantMembership(
            tenant_id=tenant.id,
            user_id=user.id,
            full_name=user.full_name,
            status="active",
        )
    )
    await db_session.flush()
    await _seed_code(db_session, user.email)
    response = await auth_client.post(
        f"{_BASE}/login/verify", json={"email": user.email, "code": "123456"}
    )
    assert response.status_code == 200
    assert "refresh_token" not in response.json()
    assert "HttpOnly" in response.headers["set-cookie"]
    assert auth_client.cookies.get("aurum_refresh_token")
    return user, response.json()["access_token"]


async def test_legacy_cutoff_recovers_same_tenant_via_refresh_cookie(
    auth_client: AsyncClient,
    token_session: tuple[AppUser, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, access = token_session
    legacy = _legacy_token(access)
    headers = {"Authorization": f"Bearer {legacy}"}
    monkeypatch.setattr(settings, "JWT_LEGACY_ACCESS_UNTIL", 0)
    assert (await auth_client.get(f"{_BASE}/me", headers=headers)).status_code == 401

    monkeypatch.setattr(settings, "JWT_LEGACY_ACCESS_UNTIL", int(time()) + 300)
    before = await auth_client.get(f"{_BASE}/me", headers=headers)
    assert before.status_code == 200
    assert before.json()["id"] == str(user.id)
    assert before.json()["active_tenant_id"] == str(user.home_tenant_id)

    monkeypatch.setattr(settings, "JWT_LEGACY_ACCESS_UNTIL", int(time()) - 1)
    assert (await auth_client.get(f"{_BASE}/me", headers=headers)).status_code == 401
    refreshed = await auth_client.post(
        f"{_BASE}/refresh", json={"operation_id": str(uuid4())}, headers=_ORIGIN
    )
    assert refreshed.status_code == 200
    new_access = refreshed.json()["access_token"]
    _assert_strict_token(new_access, user)
    after = await auth_client.get(f"{_BASE}/me", headers={"Authorization": f"Bearer {new_access}"})
    assert after.status_code == 200
    for key in ("id", "home_tenant_id", "active_tenant_id", "branch_assignments"):
        assert after.json()[key] == before.json()[key]


async def test_lost_refresh_reply_reissues_strict_access_for_same_successor(
    auth_client: AsyncClient,
    token_session: tuple[AppUser, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _ = token_session
    monkeypatch.setattr(settings, "JWT_LEGACY_ACCESS_UNTIL", 0)
    original_cookies = Cookies(auth_client.cookies)
    operation = {"operation_id": str(uuid4())}
    first = await auth_client.post(f"{_BASE}/refresh", json=operation, headers=_ORIGIN)
    assert first.status_code == 200
    first_access = first.json()["access_token"]
    first_jti = _assert_strict_token(first_access, user)
    successor_cookie = auth_client.cookies.get("aurum_refresh_token")

    # The browser never received the first response or its Set-Cookie header.
    auth_client.cookies = original_cookies
    replay = await auth_client.post(f"{_BASE}/refresh", json=operation, headers=_ORIGIN)
    assert replay.status_code == 200
    assert "refresh_token" not in replay.json()
    replay_access = replay.json()["access_token"]
    assert _assert_strict_token(replay_access, user) != first_jti
    assert decode_access_token(replay_access)["sid"] == decode_access_token(first_access)["sid"]
    # Keep opaque refresh credentials out of pytest assertion diagnostics.
    same_successor = auth_client.cookies.get("aurum_refresh_token") == successor_cookie
    assert same_successor
    me = await auth_client.get(f"{_BASE}/me", headers={"Authorization": f"Bearer {replay_access}"})
    assert me.status_code == 200
    assert me.json()["id"] == str(user.id)
    assert me.json()["active_tenant_id"] == str(user.home_tenant_id)


async def test_strict_rejection_precedes_protected_post_business_change(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    token_session: tuple[AppUser, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, access = token_session
    monkeypatch.setattr(settings, "JWT_LEGACY_ACCESS_UNTIL", 0)
    dismissed_at = select(AppUser.mfa_prompt_dismissed_at).where(AppUser.id == user.id)
    assert await db_session.scalar(dismissed_at) is None
    # auth_client overrides the dependency that normally sets the request's DB identity.
    await _actor(db_session, access)
    rejected = await auth_client.post(
        f"{_BASE}/mfa/settings/dismiss",
        headers={"Authorization": f"Bearer {_legacy_token(access)}"},
    )
    assert rejected.status_code == 401
    assert await db_session.scalar(dismissed_at) is None

    refreshed = await auth_client.post(
        f"{_BASE}/refresh", json={"operation_id": str(uuid4())}, headers=_ORIGIN
    )
    assert refreshed.status_code == 200
    new_access = refreshed.json()["access_token"]
    _assert_strict_token(new_access, user)
    await _actor(db_session, new_access)
    accepted = await auth_client.post(
        f"{_BASE}/mfa/settings/dismiss",
        headers={"Authorization": f"Bearer {new_access}"},
    )
    assert accepted.status_code == 200
    assert await db_session.scalar(dismissed_at) is not None
