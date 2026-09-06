"""Account security preferences and password proofs exercise real HTTP/DB boundaries."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.security as security_module
from app.core.deps import CurrentUser, require_recent_account_mfa
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.core.time import utc_now
from app.domains.auth.models import AppUser, EmailCode, Session
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from tests.domains.auth.test_login import _seed_code
from tests.domains.auth.test_mfa import (
    _PASSWORD,
    _enroll_support,
    _start_optional_enrollment,
    _totp_code,
)
from tests.domains.auth.test_sessions import _create_session
from tests.role_version_helpers import provision_test_owner

_BASE = "/api/v1/auth"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _actor(db_session: AsyncSession, token: str) -> None:
    claims = decode_access_token(token)
    await db_session.execute(
        text(
            "SELECT set_config('app.user_id', :user_id, true), "
            "set_config('app.auth_session_id', :session_id, true)"
        ),
        {"user_id": str(claims["sub"]), "session_id": str(claims["sid"])},
    )


async def _login(
    client: AsyncClient,
    db_session: AsyncSession,
    user: AppUser,
    *,
    password: str | None = _PASSWORD,
) -> str:
    await _seed_code(db_session, user.email)
    response = await client.post(
        f"{_BASE}/login/verify",
        json={"email": user.email, "code": "123456", "password": password},
    )
    assert response.status_code == 200
    token = str(response.json()["access_token"])
    await _actor(db_session, token)
    return token


@pytest.mark.parametrize("kind", ["ordinary", "owner", "developer", "administrator"])
async def test_unenrolled_accounts_can_login_and_decline_the_first_mfa_offer(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,  # type: ignore[no-untyped-def]
    kind: str,
) -> None:
    if kind == "owner":
        tenant = await FoundationService(FoundationRepository(db_session)).create_tenant(
            payload={"name": "Optional MFA owner", "contact_email": "optional-tenant@aurum.tj"}
        )
        user, _membership, _ownership, _role = await provision_test_owner(
            db_session,
            tenant_id=tenant.id,
            email="optional-owner@aurum.tj",
            full_name="Optional MFA owner",
        )
        user.password_hash = hash_password(_PASSWORD)
        await db_session.flush()
    else:
        user = await make_user(
            email=f"optional-{kind}@aurum.tj",
            password=_PASSWORD,
            is_developer=kind == "developer",
            is_administrator=kind == "administrator",
        )
    token = await _login(auth_client, db_session, user)
    assert decode_access_token(token).get("mfa_at") is None
    initial = await auth_client.get(f"{_BASE}/mfa/settings", headers=_headers(token))
    assert initial.status_code == 200
    assert initial.json() == {"enabled": False, "prompt_pending": True, "has_password": True}

    dismissed = await auth_client.post(f"{_BASE}/mfa/settings/dismiss", headers=_headers(token))
    assert dismissed.status_code == 200
    token = await _login(auth_client, db_session, user)
    persisted = await auth_client.get(f"{_BASE}/mfa/settings", headers=_headers(token))
    assert persisted.status_code == 200
    assert persisted.json()["prompt_pending"] is False
    assert persisted.json()["enabled"] is False


@pytest.mark.parametrize("kind", ["developer", "administrator"])
async def test_unenrolled_platform_session_is_rejected_after_logout_even_in_support_context(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,  # type: ignore[no-untyped-def]
    kind: str,
) -> None:
    user = await make_user(
        email=f"revoked-optional-{kind}@aurum.tj",
        password=_PASSWORD,
        is_developer=kind == "developer",
        is_administrator=kind == "administrator",
    )
    token = await _login(auth_client, db_session, user)
    assert decode_access_token(token).get("mfa_at") is None
    before = await auth_client.get("/api/v1/admin/tenants", headers=_headers(token))
    assert before.status_code == 200
    logged_out = await auth_client.post(
        f"{_BASE}/logout", json={}, headers={"Origin": "http://localhost:5173"}
    )
    assert logged_out.status_code == 200
    for path in (f"{_BASE}/me", "/api/v1/admin/tenants"):
        rejected = await auth_client.get(path, headers=_headers(token))
        assert rejected.status_code == 401


async def test_ordinary_user_can_enable_and_disable_mfa_with_session_revocation(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await make_user(password=_PASSWORD)
    token = await _login(auth_client, db_session, user)
    other_token = await _login(auth_client, db_session, user)
    await _actor(db_session, token)
    base_time = utc_now() - timedelta(seconds=60)
    monkeypatch.setattr(security_module, "utc_now", lambda: base_time)
    setup = await _start_optional_enrollment(auth_client, db_session, token)
    confirmed = await auth_client.post(
        f"{_BASE}/mfa/settings/enroll/confirm",
        headers=_headers(token),
        json={
            "challenge_token": setup["challenge_token"],
            "code": _totp_code(str(setup["secret"]), base_time),
        },
    )
    assert confirmed.status_code == 200
    enrolled_token = str(confirmed.json()["access_token"])
    assert decode_access_token(enrolled_token).get("mfa_at") is not None
    for old_token in (token, other_token):
        denied = await auth_client.get(f"{_BASE}/me", headers=_headers(old_token))
        assert denied.status_code == 401

    await _actor(db_session, enrolled_token)
    enabled = await auth_client.get(f"{_BASE}/mfa/settings", headers=_headers(enrolled_token))
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    assert enabled.json()["prompt_pending"] is False
    await _seed_code(db_session, user.email)
    challenge = await auth_client.post(
        f"{_BASE}/login/verify",
        json={"email": user.email, "code": "123456", "password": _PASSWORD},
    )
    assert challenge.status_code == 200
    assert challenge.json()["status"] == "mfa_required"
    assert "access_token" not in challenge.json()

    await _assert_disable_requires_password_and_mfa_session(
        auth_client, db_session, user, enrolled_token
    )
    disabled = await auth_client.post(
        f"{_BASE}/mfa/settings/disable",
        headers=_headers(enrolled_token),
        json={"password": _PASSWORD},
    )
    assert disabled.status_code == 200
    disabled_token = str(disabled.json()["access_token"])
    assert decode_access_token(disabled_token).get("mfa_at") is None
    assert decode_access_token(disabled_token)["sid"] != decode_access_token(enrolled_token)["sid"]
    denied = await auth_client.get(f"{_BASE}/me", headers=_headers(enrolled_token))
    assert denied.status_code == 401
    await _actor(db_session, disabled_token)
    state = await auth_client.get(f"{_BASE}/mfa/settings", headers=_headers(disabled_token))
    assert state.status_code == 200
    assert state.json() == {"enabled": False, "prompt_pending": False, "has_password": True}
    relogin_token = await _login(auth_client, db_session, user)
    assert decode_access_token(relogin_token).get("mfa_at") is None


async def _assert_disable_requires_password_and_mfa_session(
    client: AsyncClient, db_session: AsyncSession, user: AppUser, token: str
) -> None:
    invalid_requests = (
        ({"password": "incorrect-password"}, 401),
        ({}, 422),
    )
    for payload, expected_status in invalid_requests:
        rejected = await client.post(
            f"{_BASE}/mfa/settings/disable", headers=_headers(token), json=payload
        )
        assert rejected.status_code == expected_status
    unverified_session = await _create_session(db_session, user=user)
    unverified_token = create_access_token(
        user.id,
        tenant_id=None,
        is_developer=False,
        is_administrator=False,
        session_id=unverified_session.id,
    )
    await _actor(db_session, unverified_token)
    rejected = await client.post(
        f"{_BASE}/mfa/settings/disable",
        headers=_headers(unverified_token),
        json={"password": _PASSWORD},
    )
    assert rejected.status_code == 401
    await _actor(db_session, token)


async def test_mfa_disable_password_attempt_limit_prevents_brute_force(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enrolled = await _enroll_support(
        auth_client=auth_client,
        db_session=db_session,
        make_user=make_user,
        monkeypatch=monkeypatch,
    )
    await _actor(db_session, enrolled.access_token)
    for _attempt in range(5):
        rejected = await auth_client.post(
            f"{_BASE}/mfa/settings/disable",
            headers=_headers(enrolled.access_token),
            json={"password": "incorrect-password"},
        )
        assert rejected.status_code == 401
    blocked = await auth_client.post(
        f"{_BASE}/mfa/settings/disable",
        headers=_headers(enrolled.access_token),
        json={"password": _PASSWORD},
    )
    assert blocked.status_code == 429
    assert blocked.json()["error"]["details"]["retry_after_minutes"] == 15
    state = await auth_client.get(f"{_BASE}/mfa/settings", headers=_headers(enrolled.access_token))
    assert state.status_code == 200
    assert state.json()["enabled"] is True


async def test_mfa_enrollment_rejects_wrong_password_and_wrong_totp(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,  # type: ignore[no-untyped-def]
) -> None:
    user = await make_user(password=_PASSWORD)
    token = await _login(auth_client, db_session, user)
    rejected = await auth_client.post(
        f"{_BASE}/mfa/settings/enroll/start",
        headers=_headers(token),
        json={"password": "incorrect-password"},
    )
    assert rejected.status_code == 401
    setup = await _start_optional_enrollment(auth_client, db_session, token)
    instant = utc_now()
    valid_codes = {
        _totp_code(str(setup["secret"]), instant + timedelta(seconds=offset))
        for offset in (-30, 0, 30)
    }
    invalid_code = next(
        f"{number:06d}" for number in range(4) if f"{number:06d}" not in valid_codes
    )
    rejected = await auth_client.post(
        f"{_BASE}/mfa/settings/enroll/confirm",
        headers=_headers(token),
        json={"challenge_token": setup["challenge_token"], "code": invalid_code},
    )
    assert rejected.status_code == 401
    state = await auth_client.get(f"{_BASE}/mfa/settings", headers=_headers(token))
    assert state.status_code == 200
    assert state.json()["enabled"] is False


async def test_password_setup_requires_email_proof_and_cannot_replace_existing_password(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,  # type: ignore[no-untyped-def]
) -> None:
    user = await make_user()
    token = await _login(auth_client, db_session, user, password=None)
    initial = await auth_client.get(f"{_BASE}/mfa/settings", headers=_headers(token))
    assert initial.json()["has_password"] is False
    blocked_enrollment = await auth_client.post(
        f"{_BASE}/mfa/settings/enroll/start",
        headers=_headers(token),
        json={"password": _PASSWORD},
    )
    assert blocked_enrollment.status_code == 403
    assert blocked_enrollment.json()["error"]["details"]["reason"] == "password_setup_required"
    used_code = await db_session.scalar(
        select(EmailCode).where(EmailCode.email_lower == user.email)
    )
    assert used_code is not None
    used_code.created_at = utc_now() - timedelta(seconds=61)
    await db_session.flush()
    code_response = await auth_client.post(f"{_BASE}/password/setup/code", headers=_headers(token))
    assert code_response.status_code == 202
    code = str(code_response.json()["dev_code"])
    wrong_code = "000000" if code != "000000" else "999999"
    wrong = await auth_client.post(
        f"{_BASE}/password/setup",
        headers=_headers(token),
        json={"code": wrong_code, "new_password": _PASSWORD},
    )
    assert wrong.status_code == 401
    for invalid_password in ("short", "x" * 129):
        invalid = await auth_client.post(
            f"{_BASE}/password/setup",
            headers=_headers(token),
            json={"code": code, "new_password": invalid_password},
        )
        assert invalid.status_code == 422
    created = await auth_client.post(
        f"{_BASE}/password/setup",
        headers=_headers(token),
        json={"code": code, "new_password": _PASSWORD},
    )
    assert created.status_code == 200
    await db_session.refresh(user)
    assert user.password_hash is not None
    assert verify_password(_PASSWORD, user.password_hash)
    relogin_token = await _login(auth_client, db_session, user)
    overwritten = await auth_client.post(
        f"{_BASE}/password/setup",
        headers=_headers(relogin_token),
        json={"code": code, "new_password": "Unrequested-Replacement-Password"},
    )
    assert overwritten.status_code == 409
    await db_session.refresh(user)
    assert user.password_hash is not None
    assert verify_password(_PASSWORD, user.password_hash)


async def test_password_confirmation_grants_distinct_recent_proof_without_mfa_or_cookie_rotation(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,  # type: ignore[no-untyped-def]
) -> None:
    user = await make_user(password=_PASSWORD)
    token = await _login(auth_client, db_session, user)
    original_cookie = auth_client.cookies.get("aurum_refresh_token")
    unbacked_proof = create_access_token(
        user.id,
        tenant_id=None,
        is_developer=False,
        is_administrator=False,
        session_id=UUID(str(decode_access_token(token)["sid"])),
        password_verified_at=utc_now(),
    )
    denied = await auth_client.get(f"{_BASE}/me", headers=_headers(unbacked_proof))
    assert denied.status_code == 401
    rejected = await auth_client.post(
        f"{_BASE}/password/confirm",
        headers=_headers(token),
        json={"password": "incorrect-password"},
    )
    assert rejected.status_code == 401
    confirmed = await auth_client.post(
        f"{_BASE}/password/confirm", headers=_headers(token), json={"password": _PASSWORD}
    )
    assert confirmed.status_code == 200
    claims = decode_access_token(str(confirmed.json()["access_token"]))
    assert claims.get("password_at") is not None
    assert claims.get("mfa_at") is None
    assert claims["sid"] == decode_access_token(token)["sid"]
    assert auth_client.cookies.get("aurum_refresh_token") == original_cookie
    session = await db_session.get(Session, UUID(str(claims["sid"])))
    assert session is not None
    await db_session.refresh(session)
    assert session.password_verified_at is not None
    assert session.mfa_verified_at is None
    critical_user = CurrentUser(
        user_id=user.id,
        tenant_id=None,
        is_developer=False,
        is_administrator=False,
        session_id=session.id,
        password_verified_at=session.password_verified_at,
    )
    assert await require_recent_account_mfa(critical_user) is critical_user
    refreshed = await auth_client.post(
        f"{_BASE}/refresh", json={}, headers={"Origin": "http://localhost:5173"}
    )
    assert refreshed.status_code == 200
    refreshed_claims = decode_access_token(str(refreshed.json()["access_token"]))
    assert refreshed_claims.get("password_at") is None
    assert refreshed_claims.get("mfa_at") is None
    assert refreshed_claims["sid"] != claims["sid"]


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/mfa/settings", None),
        ("POST", "/mfa/settings/dismiss", None),
        ("POST", "/mfa/settings/enroll/start", {"password": _PASSWORD}),
        ("POST", "/mfa/settings/disable", {"password": _PASSWORD}),
        ("POST", "/password/setup/code", None),
        ("POST", "/password/setup", {"code": "123456", "new_password": _PASSWORD}),
        ("POST", "/password/confirm", {"password": _PASSWORD}),
    ],
)
async def test_account_security_endpoints_require_authentication(
    auth_client: AsyncClient,
    method: str,
    path: str,
    payload: dict[str, str] | None,
) -> None:
    response = await auth_client.request(method, f"{_BASE}{path}", json=payload)
    assert response.status_code == 401
