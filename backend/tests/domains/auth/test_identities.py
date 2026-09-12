"""Credential, contact ownership, and recovery boundaries against PostgreSQL."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    derive_mfa_encryption_key,
    generate_totp_secret,
)
from app.core.time import utc_now
from app.domains.auth.identity_schemas import PasswordResetRequest
from app.domains.auth.schemas import PasswordSetupRequest
from tests.domains.auth.test_login import _seed_code
from tests.domains.auth.test_mfa import _totp_code
from tests.domains.auth.test_optional_mfa import _actor
from tests.domains.auth.test_sessions import _auth_headers, _create_session

BASE = "/api/v1/auth"
PASSWORD = "Identity-test-password-42!"
PHONE = "+992900000001"


@pytest_asyncio.fixture
async def db_engine(maintenance_engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    """Only test setup needs direct access to protected identity challenge rows.

    Runtime grants are asserted separately; HTTP uses the normal dependency override.
    """
    yield maintenance_engine


@pytest.fixture(autouse=True)
def identity_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "SMS_PROVIDER", "development")
    monkeypatch.setattr("app.domains.auth.identity_service.generate_email_code", lambda: "123456")


async def _verified(db: AsyncSession, user_id: UUID) -> None:
    await db.execute(
        text(
            "UPDATE app_user SET verified_phone=:phone,phone_verified_at=now(),"
            "email_verified_at=now() WHERE id=:id"
        ),
        {"phone": PHONE, "id": user_id},
    )


async def _factor(db: AsyncSession, user_id: UUID) -> str:
    secret = generate_totp_secret()
    await db.execute(
        text("""INSERT INTO support_mfa(user_id,status,active_secret_ciphertext,
    active_key_version,active_generation,confirmed_at)
    VALUES(:user,'active',pgp_sym_encrypt(:secret,:key),1,1,now())"""),
        {"user": user_id, "secret": secret, "key": derive_mfa_encryption_key()},
    )
    return secret


async def test_email_password_issues_same_account_without_stepup(
    auth_client: AsyncClient, make_user
) -> None:
    user = await make_user(password=PASSWORD)
    result = await auth_client.post(
        f"{BASE}/login/password", json={"email": user.email, "password": PASSWORD}
    )
    assert result.status_code == 200, result.text
    claims = decode_access_token(result.json()["access_token"])
    assert claims["sub"] == str(user.id)
    assert claims.get("password_at") is None and claims.get("mfa_at") is None
    assert "HttpOnly" in result.headers["set-cookie"]
    assert result.headers["cache-control"] == "no-store"


async def test_email_otp_is_bootstrap_only(
    auth_client: AsyncClient, db_session: AsyncSession, make_user
) -> None:
    user = await make_user(password=PASSWORD)
    await _seed_code(db_session, user.email)
    response = await auth_client.post(
        f"{BASE}/login/verify", json={"email": user.email, "code": "123456", "password": PASSWORD}
    )
    assert response.status_code == 401
    bootstrap = await make_user()
    await _seed_code(db_session, bootstrap.email)
    response = await auth_client.post(
        f"{BASE}/login/verify", json={"email": bootstrap.email, "code": "123456"}
    )
    assert response.status_code == 200, response.text
    assert await db_session.scalar(
        text("SELECT email_verified_at IS NOT NULL FROM app_user WHERE id=:id"),
        {"id": bootstrap.id},
    )


async def test_phone_login_requires_verified_contact_and_consumes_once(
    auth_client: AsyncClient, db_session: AsyncSession, make_user
) -> None:
    user = await make_user(password=PASSWORD)
    await db_session.execute(
        text("UPDATE app_user SET phone=:phone WHERE id=:id"), {"phone": PHONE, "id": user.id}
    )
    await auth_client.post(f"{BASE}/login/phone/code", json={"phone": PHONE})
    denied = await auth_client.post(
        f"{BASE}/login/phone/verify", json={"phone": PHONE, "code": "123456"}
    )
    assert denied.status_code == 401
    await _verified(db_session, user.id)
    await db_session.execute(
        text("UPDATE auth_identity_code SET created_at=now()-INTERVAL '2 minutes'")
    )
    issued = await auth_client.post(f"{BASE}/login/phone/code", json={"phone": PHONE})
    assert issued.status_code == 202, issued.text
    result = await auth_client.post(
        f"{BASE}/login/phone/verify", json={"phone": PHONE, "code": "123456"}
    )
    assert result.status_code == 200, result.text
    assert decode_access_token(result.json()["access_token"])["sub"] == str(user.id)
    replay = await auth_client.post(
        f"{BASE}/login/phone/verify", json={"phone": PHONE, "code": "123456"}
    )
    assert replay.status_code == 401


async def test_phone_login_keeps_enabled_mfa(
    auth_client: AsyncClient, db_session: AsyncSession, make_user
) -> None:
    user = await make_user(password=PASSWORD)
    await _verified(db_session, user.id)
    await _factor(db_session, user.id)
    await auth_client.post(f"{BASE}/login/phone/code", json={"phone": PHONE})
    result = await auth_client.post(
        f"{BASE}/login/phone/verify", json={"phone": PHONE, "code": "123456"}
    )
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "mfa_required"
    assert "access_token" not in result.json()


async def test_reset_is_purpose_bound_and_revokes_sessions(
    auth_client: AsyncClient, db_session: AsyncSession, make_user
) -> None:
    user = await make_user(password=PASSWORD)
    await _verified(db_session, user.id)
    original = await _create_session(db_session, user=user)
    issued = await auth_client.post(
        f"{BASE}/password/reset/code", json={"channel": "phone", "contact": PHONE}
    )
    assert issued.status_code == 202, issued.text
    wrong_purpose = await auth_client.post(
        f"{BASE}/login/phone/verify", json={"phone": PHONE, "code": "123456"}
    )
    assert wrong_purpose.status_code == 401
    payload = {
        "channel": "phone",
        "contact": PHONE,
        "code": "123456",
        "new_password": "Replacement-password-55!",
    }
    result = await auth_client.post(f"{BASE}/password/reset", json=payload)
    assert result.status_code == 200, result.text
    assert await db_session.scalar(
        text("SELECT revoked_at IS NOT NULL FROM session WHERE id=:id"), {"id": original.id}
    )
    replay = await auth_client.post(f"{BASE}/password/reset", json=payload)
    assert replay.status_code == 401
    login = await auth_client.post(
        f"{BASE}/login/password", json={"email": user.email, "password": payload["new_password"]}
    )
    assert login.status_code == 200, login.text


async def test_reset_mfa_cannot_be_omitted(
    auth_client: AsyncClient, db_session: AsyncSession, make_user
) -> None:
    user = await make_user(password=PASSWORD)
    await _verified(db_session, user.id)
    secret = await _factor(db_session, user.id)
    await auth_client.post(
        f"{BASE}/password/reset/code", json={"channel": "phone", "contact": PHONE}
    )
    payload = {
        "channel": "phone",
        "contact": PHONE,
        "code": "123456",
        "new_password": "Replacement-password-55!",
    }
    denied = await auth_client.post(f"{BASE}/password/reset", json=payload)
    assert denied.status_code == 401
    assert denied.json()["error"]["details"]["reason"] == "mfa_required"
    result = await auth_client.post(
        f"{BASE}/password/reset", json={**payload, "mfa_code": _totp_code(secret, utc_now())}
    )
    assert result.status_code == 200, result.text
    assert (
        await db_session.scalar(
            text("SELECT status FROM support_mfa WHERE user_id=:id"), {"id": user.id}
        )
        == "active"
    )


async def test_phone_change_requires_current_identity_and_preserves_current_session(
    auth_client: AsyncClient, db_session: AsyncSession, make_user
) -> None:
    user = await make_user(password=PASSWORD)
    current = await _create_session(db_session, user=user)
    other = await _create_session(db_session, user=user)
    headers = _auth_headers(user, current)
    await _actor(db_session, headers["Authorization"].removeprefix("Bearer "))
    denied = await auth_client.post(f"{BASE}/phone/code", headers=headers, json={"phone": PHONE})
    assert denied.status_code == 401
    issued = await auth_client.post(
        f"{BASE}/phone/code", headers=headers, json={"phone": PHONE, "password": PASSWORD}
    )
    assert issued.status_code == 202, issued.text
    verified = await auth_client.post(
        f"{BASE}/phone/verify", headers=headers, json={"phone": PHONE, "code": "123456"}
    )
    assert verified.status_code == 200, verified.text
    assert (
        await db_session.scalar(
            text("SELECT verified_phone FROM app_user WHERE id=:id"), {"id": user.id}
        )
        == PHONE
    )
    assert await db_session.scalar(
        text("SELECT revoked_at IS NULL FROM session WHERE id=:id"), {"id": current.id}
    )
    assert await db_session.scalar(
        text("SELECT revoked_at IS NOT NULL FROM session WHERE id=:id"), {"id": other.id}
    )


async def test_password_setup_via_verified_phone_and_prompt_dismiss(
    auth_client: AsyncClient, db_session: AsyncSession, make_user
) -> None:
    user = await make_user()
    await _verified(db_session, user.id)
    current = await _create_session(db_session, user=user)
    headers = _auth_headers(user, current)
    await _actor(db_session, headers["Authorization"].removeprefix("Bearer "))
    identity = await auth_client.get(f"{BASE}/identity", headers=headers)
    assert identity.json()["password_prompt_pending"] is True
    assert (
        await auth_client.post(f"{BASE}/password/prompt/dismiss", headers=headers)
    ).status_code == 200
    assert (await auth_client.get(f"{BASE}/identity", headers=headers)).json()[
        "password_prompt_pending"
    ] is False
    issued = await auth_client.post(
        f"{BASE}/password/setup/code", headers=headers, json={"channel": "phone"}
    )
    assert issued.status_code == 202, issued.text
    result = await auth_client.post(
        f"{BASE}/password/setup",
        headers=headers,
        json={"channel": "phone", "code": "123456", "new_password": PASSWORD},
    )
    assert result.status_code == 200, result.text


async def test_credential_functions_unavailable_to_tenant_role(db_session: AsyncSession) -> None:
    for signature in (
        "lookup_identity_password(text)",
        "create_identity_login_proof(text,text,uuid,text,text,text,text)",
    ):
        assert not await db_session.scalar(
            text("SELECT has_function_privilege('aurum_app',:signature,'EXECUTE')"),
            {"signature": signature},
        )


async def test_wrong_sms_codes_have_five_attempt_budget(
    auth_client: AsyncClient, db_session: AsyncSession, make_user
) -> None:
    user = await make_user()
    await _verified(db_session, user.id)
    await auth_client.post(f"{BASE}/login/phone/code", json={"phone": PHONE})
    for _ in range(5):
        assert (
            await auth_client.post(
                f"{BASE}/login/phone/verify", json={"phone": PHONE, "code": "999999"}
            )
        ).status_code == 401
    assert (
        await auth_client.post(
            f"{BASE}/login/phone/verify", json={"phone": PHONE, "code": "123456"}
        )
    ).status_code == 429


async def test_disabled_sms_has_explicit_availability(
    auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "SMS_PROVIDER", "disabled")
    assert (await auth_client.get(f"{BASE}/methods")).json() == {"phone_login_enabled": False}
    assert (
        await auth_client.post(f"{BASE}/login/phone/code", json={"phone": PHONE})
    ).status_code == 503


@pytest.mark.parametrize("password", ["a" * 73, "я" * 37])
def test_new_passwords_cannot_be_silently_truncated(password: str) -> None:
    with pytest.raises(ValidationError, match="72 UTF-8 bytes"):
        PasswordSetupRequest(code="123456", new_password=password)
    with pytest.raises(ValidationError, match="72 UTF-8 bytes"):
        PasswordResetRequest(channel="phone", contact=PHONE, code="123456", new_password=password)


async def test_invalid_nul_password_is_rejected_without_server_error(
    auth_client: AsyncClient, make_user
) -> None:
    user = await make_user(password=PASSWORD)
    result = await auth_client.post(
        f"{BASE}/login/password", json={"email": user.email, "password": "invalid\x00password"}
    )
    assert result.status_code == 401
    with pytest.raises(ValidationError, match="NUL"):
        PasswordSetupRequest(code="123456", new_password="new-password\x00-invalid")
    with pytest.raises(ValidationError, match="NUL"):
        PasswordResetRequest(
            channel="phone", contact=PHONE, code="123456", new_password="new-password\x00-invalid"
        )


async def test_phone_binding_uses_fresh_totp_without_refresh_stepup(
    auth_client: AsyncClient, db_session: AsyncSession, make_user
) -> None:
    user = await make_user(password=PASSWORD)
    current = await _create_session(db_session, user=user)
    secret = await _factor(db_session, user.id)
    baseline = utc_now() - timedelta(minutes=15)
    current.mfa_verified_at = baseline
    await db_session.flush()
    token = create_access_token(
        user.id,
        tenant_id=None,
        is_developer=False,
        is_administrator=False,
        session_id=current.id,
        mfa_verified_at=baseline,
    )
    await _actor(db_session, token)
    headers = {"Authorization": f"Bearer {token}"}
    result = await auth_client.post(
        f"{BASE}/phone/code",
        headers=headers,
        json={"phone": PHONE, "password": PASSWORD, "mfa_code": _totp_code(secret, utc_now())},
    )
    assert result.status_code == 202, result.text
    assert (
        await db_session.scalar(
            text("SELECT mfa_verified_at FROM session WHERE id=:id"), {"id": current.id}
        )
        == baseline
    )
    confirmed = await auth_client.post(
        f"{BASE}/phone/verify", headers=headers, json={"phone": PHONE, "code": "123456"}
    )
    assert confirmed.status_code == 200, confirmed.text
