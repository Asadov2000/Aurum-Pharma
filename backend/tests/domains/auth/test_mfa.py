"""End-to-end support-account MFA flows against the database functions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

import app.core.security as security_module
from app.core.security import (
    create_access_token,
    decode_access_token,
    derive_mfa_encryption_key,
    generate_code_salt,
    hash_code,
    hash_password,
    hash_recovery_code,
    hash_token,
    mfa_encryption_keyring_json,
)
from app.core.time import utc_now
from app.domains.auth.models import (
    AppUser,
    AuthMfaChallenge,
    Session,
    SupportMfa,
    SupportMfaRecoveryCode,
)
from app.main import app
from tests.domains.auth.test_login import _seed_code

_PASSWORD = "Very-Strong-Test-Password-42"


@dataclass(frozen=True)
class EnrolledSupport:
    user: AppUser
    base_time: datetime
    secret: str
    first_totp: str
    recovery_codes: list[str]
    access_token: str
    refresh_token: str


def _totp_code(secret: str, instant: datetime) -> str:
    padding = "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(secret + padding)
    counter = int(instant.timestamp()) // 30
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFF_FFFF
    return f"{binary % 1_000_000:06d}"


async def _new_mfa_challenge(
    *,
    auth_client: AsyncClient,
    db_session: AsyncSession,
    user: AppUser,
) -> str:
    await _seed_code(db_session, user.email, code="123456")
    response = await auth_client.post(
        "/api/v1/auth/login/verify",
        json={
            "email": user.email,
            "code": "123456",
            "password": _PASSWORD,
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] in {
        "mfa_required",
        "mfa_recovery_required",
    }
    return str(response.json()["challenge_token"])


async def _enroll_support(
    *,
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> EnrolledSupport:
    user = await make_user(
        email="mfa-developer@aurum.tj",
        password=_PASSWORD,
        is_developer=True,
    )
    # Keep deterministic counter changes in the past so JWT ``iat`` validation
    # remains valid while the TOTP clock advances across test steps.
    base_time = utc_now() - timedelta(seconds=90)
    monkeypatch.setattr(security_module, "utc_now", lambda: base_time)

    await _seed_code(db_session, user.email, code="123456")
    login = await auth_client.post(
        "/api/v1/auth/login/verify",
        json={
            "email": user.email,
            "code": "123456",
            "password": _PASSWORD,
        },
    )
    assert login.status_code == 200
    assert login.json()["status"] == "mfa_enrollment_required"
    assert "access_token" not in login.json()
    challenge_token = str(login.json()["challenge_token"])

    start = await auth_client.post(
        "/api/v1/auth/mfa/enroll/start",
        json={"challenge_token": challenge_token},
    )
    assert start.status_code == 200
    setup = start.json()
    secret = str(setup["secret"])
    recovery_codes = [str(code) for code in setup["recovery_codes"]]
    first_totp = _totp_code(secret, base_time)

    confirm = await auth_client.post(
        "/api/v1/auth/mfa/enroll/confirm",
        json={"challenge_token": challenge_token, "code": first_totp},
    )
    assert confirm.status_code == 200
    access_token = str(confirm.json()["access_token"])
    refresh_token = auth_client.cookies.get("aurum_refresh_token")
    assert refresh_token is not None
    return EnrolledSupport(
        user=user,
        base_time=base_time,
        secret=secret,
        first_totp=first_totp,
        recovery_codes=recovery_codes,
        access_token=access_token,
        refresh_token=refresh_token,
    )


async def test_support_login_requires_password_before_mfa_challenge(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,  # type: ignore[no-untyped-def]
) -> None:
    user = await make_user(
        email="mfa-password@aurum.tj",
        password=_PASSWORD,
        is_administrator=True,
    )
    await _seed_code(db_session, user.email, code="123456")

    missing = await auth_client.post(
        "/api/v1/auth/login/verify",
        json={"email": user.email, "code": "123456"},
    )
    wrong = await auth_client.post(
        "/api/v1/auth/login/verify",
        json={
            "email": user.email,
            "code": "123456",
            "password": "wrong-password",
        },
    )
    accepted = await auth_client.post(
        "/api/v1/auth/login/verify",
        json={
            "email": user.email,
            "code": "123456",
            "password": _PASSWORD,
        },
    )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "mfa_enrollment_required"
    assert "access_token" not in accepted.json()
    assert "aurum_refresh_token=" not in accepted.headers.get("set-cookie", "")
    assert accepted.headers["cache-control"] == "no-store"
    assert accepted.headers["pragma"] == "no-cache"


async def test_mfa_enrollment_encrypts_secret_and_activates_recovery_codes(
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

    mfa = await db_session.get(SupportMfa, enrolled.user.id)
    recovery_rows = (
        (
            await db_session.execute(
                select(SupportMfaRecoveryCode).where(
                    SupportMfaRecoveryCode.user_id == enrolled.user.id
                )
            )
        )
        .scalars()
        .all()
    )
    claims = decode_access_token(enrolled.access_token)

    assert mfa is not None
    assert mfa.status == "active"
    assert mfa.active_secret_ciphertext is not None
    assert enrolled.secret.encode() not in bytes(mfa.active_secret_ciphertext)
    assert mfa.pending_secret_ciphertext is None
    assert mfa.active_generation == 1
    assert mfa.last_used_counter == int(enrolled.base_time.timestamp()) // 30
    assert len(recovery_rows) == 10
    assert all(row.activated_at is not None and row.used_at is None for row in recovery_rows)
    assert {row.code_hash for row in recovery_rows} == {
        hash_recovery_code(code) for code in enrolled.recovery_codes
    }
    stored_hashes = {row.code_hash for row in recovery_rows}
    assert all(code not in stored_hashes for code in enrolled.recovery_codes)
    assert claims["sid"]
    assert claims["mfa_at"]

    refreshed = await auth_client.post(
        "/api/v1/auth/refresh",
        json={},
        headers={"Origin": "http://localhost:5173"},
    )
    assert refreshed.status_code == 200
    refreshed_claims = decode_access_token(refreshed.json()["access_token"])
    assert refreshed_claims["sid"] != claims["sid"]
    assert refreshed_claims["mfa_at"] == claims["mfa_at"]
    assert refreshed.headers["cache-control"] == "no-store"


async def test_totp_replay_is_rejected_and_revoked_support_session_stops_immediately(
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
    challenge_token = await _new_mfa_challenge(
        auth_client=auth_client,
        db_session=db_session,
        user=enrolled.user,
    )
    next_time = enrolled.base_time + timedelta(seconds=30)
    monkeypatch.setattr(security_module, "utc_now", lambda: next_time)

    replay = await auth_client.post(
        "/api/v1/auth/mfa/verify",
        json={
            "challenge_token": challenge_token,
            "code": enrolled.first_totp,
        },
    )
    assert replay.status_code == 401

    next_totp = _totp_code(enrolled.secret, next_time)
    verified = await auth_client.post(
        "/api/v1/auth/mfa/verify",
        json={"challenge_token": challenge_token, "code": next_totp},
    )
    assert verified.status_code == 200
    verified_token = str(verified.json()["access_token"])
    verified_claims = decode_access_token(verified_token)
    consumed = await auth_client.post(
        "/api/v1/auth/mfa/verify",
        json={"challenge_token": challenge_token, "code": next_totp},
    )
    assert consumed.status_code == 401

    await db_session.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": str(enrolled.user.id)},
    )
    step_up_replay = await auth_client.post(
        "/api/v1/auth/mfa/step-up",
        json={"code": next_totp},
        headers={"Authorization": f"Bearer {verified_token}"},
    )
    assert step_up_replay.status_code == 401

    step_up_time = next_time + timedelta(seconds=30)
    monkeypatch.setattr(security_module, "utc_now", lambda: step_up_time)
    stepped_up = await auth_client.post(
        "/api/v1/auth/mfa/step-up",
        json={"code": _totp_code(enrolled.secret, step_up_time)},
        headers={"Authorization": f"Bearer {verified_token}"},
    )
    assert stepped_up.status_code == 200
    stepped_up_token = str(stepped_up.json()["access_token"])
    stepped_up_claims = decode_access_token(stepped_up_token)
    assert stepped_up_claims["mfa_at"] > verified_claims["mfa_at"]

    refreshed = await auth_client.post(
        "/api/v1/auth/refresh",
        json={},
        headers={"Origin": "http://localhost:5173"},
    )
    assert refreshed.status_code == 200
    refreshed_token = str(refreshed.json()["access_token"])
    refreshed_claims = decode_access_token(refreshed_token)
    assert refreshed_claims["mfa_at"] == verified_claims["mfa_at"]
    assert refreshed_claims["mfa_at"] < stepped_up_claims["mfa_at"]
    session_id = str(refreshed_claims["sid"])

    await db_session.execute(
        text(
            "UPDATE public.session "
            "SET revoked_at = now(), revoked_reason = 'test_revocation' "
            "WHERE id = :session_id"
        ),
        {"session_id": session_id},
    )
    rejected = await auth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {refreshed_token}"},
    )
    assert rejected.status_code == 401
    assert rejected.json()["error"]["code"] == "authentication_required"


async def test_recovery_code_revokes_sessions_and_requires_factor_replacement(
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
    challenge_token = await _new_mfa_challenge(
        auth_client=auth_client,
        db_session=db_session,
        user=enrolled.user,
    )

    recovered = await auth_client.post(
        "/api/v1/auth/mfa/recover",
        json={
            "challenge_token": challenge_token,
            "recovery_code": enrolled.recovery_codes[0],
        },
    )
    assert recovered.status_code == 200
    assert recovered.json()["status"] == "mfa_enrollment_required"

    old_refresh = await auth_client.post(
        "/api/v1/auth/refresh",
        json={},
        headers={"Origin": "http://localhost:5173"},
    )
    assert old_refresh.status_code == 401

    # Simulate a reload after successful recovery. A fresh email/password
    # challenge may reuse the same recovery code until replacement TOTP
    # enrollment completes; the code becomes spent only on that completion.
    challenge_token = await _new_mfa_challenge(
        auth_client=auth_client,
        db_session=db_session,
        user=enrolled.user,
    )
    resumed = await auth_client.post(
        "/api/v1/auth/mfa/recover",
        json={
            "challenge_token": challenge_token,
            "recovery_code": enrolled.recovery_codes[0],
        },
    )
    assert resumed.status_code == 200

    replacement = await auth_client.post(
        "/api/v1/auth/mfa/enroll/start",
        json={"challenge_token": challenge_token},
    )
    assert replacement.status_code == 200
    replacement_secret = str(replacement.json()["secret"])
    assert replacement_secret != enrolled.secret
    replacement_time = enrolled.base_time + timedelta(seconds=60)
    monkeypatch.setattr(security_module, "utc_now", lambda: replacement_time)
    replacement_confirmed = await auth_client.post(
        "/api/v1/auth/mfa/enroll/confirm",
        json={
            "challenge_token": challenge_token,
            "code": _totp_code(replacement_secret, replacement_time),
        },
    )
    assert replacement_confirmed.status_code == 200

    mfa = await db_session.get(SupportMfa, enrolled.user.id)
    used_code = (
        await db_session.execute(
            select(SupportMfaRecoveryCode).where(
                SupportMfaRecoveryCode.user_id == enrolled.user.id,
                SupportMfaRecoveryCode.code_hash == hash_recovery_code(enrolled.recovery_codes[0]),
            )
        )
    ).scalar_one()
    assert mfa is not None
    assert mfa.status == "active"
    assert mfa.active_generation == 2
    assert used_code.used_at is not None

    later_challenge = await _new_mfa_challenge(
        auth_client=auth_client,
        db_session=db_session,
        user=enrolled.user,
    )
    reused = await auth_client.post(
        "/api/v1/auth/mfa/recover",
        json={
            "challenge_token": later_challenge,
            "recovery_code": enrolled.recovery_codes[0],
        },
    )
    assert reused.status_code == 401


async def test_mfa_encryption_key_rotation_keeps_existing_factor_usable(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_root = "old-mfa-root-" + "o" * 40
    new_root = "new-mfa-root-" + "n" * 40
    monkeypatch.setattr(
        security_module.settings,
        "MFA_ENCRYPTION_KEY",
        SecretStr(old_root),
    )
    monkeypatch.setattr(
        security_module.settings,
        "MFA_ENCRYPTION_KEY_VERSION",
        1,
    )
    monkeypatch.setattr(
        security_module.settings,
        "MFA_ENCRYPTION_PREVIOUS_KEYS",
        {},
    )
    enrolled = await _enroll_support(
        auth_client=auth_client,
        db_session=db_session,
        make_user=make_user,
        monkeypatch=monkeypatch,
    )

    monkeypatch.setattr(
        security_module.settings,
        "MFA_ENCRYPTION_KEY",
        SecretStr(new_root),
    )
    monkeypatch.setattr(
        security_module.settings,
        "MFA_ENCRYPTION_KEY_VERSION",
        2,
    )
    monkeypatch.setattr(
        security_module.settings,
        "MFA_ENCRYPTION_PREVIOUS_KEYS",
        {1: SecretStr(old_root)},
    )
    parameters = {
        "from_version": 1,
        "to_version": 2,
        "to_key": derive_mfa_encryption_key(),
        "keyring": mfa_encryption_keyring_json(),
    }
    rotated = (
        await db_session.execute(
            text(
                "SELECT public.rotate_support_mfa_encryption("
                ":from_version, :to_version, :to_key, CAST(:keyring AS JSONB))"
            ),
            parameters,
        )
    ).scalar_one()
    repeated = (
        await db_session.execute(
            text(
                "SELECT public.rotate_support_mfa_encryption("
                ":from_version, :to_version, :to_key, CAST(:keyring AS JSONB))"
            ),
            parameters,
        )
    ).scalar_one()
    mfa = await db_session.get(SupportMfa, enrolled.user.id)

    assert rotated == 1
    assert repeated == 0
    assert mfa is not None
    assert mfa.active_key_version == 2
    assert "1" in json.loads(mfa_encryption_keyring_json())

    challenge_token = await _new_mfa_challenge(
        auth_client=auth_client,
        db_session=db_session,
        user=enrolled.user,
    )
    verify_time = enrolled.base_time + timedelta(seconds=30)
    monkeypatch.setattr(security_module, "utc_now", lambda: verify_time)
    verified = await auth_client.post(
        "/api/v1/auth/mfa/verify",
        json={
            "challenge_token": challenge_token,
            "code": _totp_code(enrolled.secret, verify_time),
        },
    )
    assert verified.status_code == 200


async def test_mfa_challenge_is_consumed_after_five_invalid_codes(
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
    challenge_token = await _new_mfa_challenge(
        auth_client=auth_client,
        db_session=db_session,
        user=enrolled.user,
    )

    for _ in range(5):
        response = await auth_client.post(
            "/api/v1/auth/mfa/verify",
            json={"challenge_token": challenge_token, "code": "000000"},
        )
        assert response.status_code == 401

    challenge = (
        await db_session.execute(
            select(AuthMfaChallenge).where(
                AuthMfaChallenge.token_hash == hash_token(challenge_token)
            )
        )
    ).scalar_one()
    assert challenge.failed_attempts == 5
    assert challenge.consumed_at is not None

    valid_after_limit = await auth_client.post(
        "/api/v1/auth/mfa/verify",
        json={
            "challenge_token": challenge_token,
            "code": _totp_code(enrolled.secret, enrolled.base_time + timedelta(seconds=30)),
        },
    )
    assert valid_after_limit.status_code == 401

    active_sessions = (
        (
            await db_session.execute(
                select(Session).where(
                    Session.user_id == enrolled.user.id,
                    Session.revoked_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(active_sessions) == 1


async def test_step_up_attempt_budget_cannot_be_bypassed_by_changing_ip(
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
    headers = {"Authorization": f"Bearer {enrolled.access_token}"}
    valid_window_codes = {
        _totp_code(enrolled.secret, enrolled.base_time + timedelta(seconds=offset))
        for offset in (-30, 0, 30)
    }
    invalid_code = next(
        f"{candidate:06d}"
        for candidate in range(1_000_000)
        if f"{candidate:06d}" not in valid_window_codes
    )
    await db_session.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": str(enrolled.user.id)},
    )

    first_transport = ASGITransport(app=app, client=("198.51.100.10", 41000))
    async with AsyncClient(
        transport=first_transport,
        base_url="http://testserver",
    ) as first_ip_client:
        for _ in range(5):
            response = await first_ip_client.post(
                "/api/v1/auth/mfa/step-up",
                json={"code": invalid_code},
                headers=headers,
            )
            assert response.status_code == 401

    second_transport = ASGITransport(app=app, client=("203.0.113.20", 42000))
    async with AsyncClient(
        transport=second_transport,
        base_url="http://testserver",
    ) as second_ip_client:
        blocked = await second_ip_client.post(
            "/api/v1/auth/mfa/step-up",
            json={"code": invalid_code},
            headers=headers,
        )

    assert blocked.status_code == 429
    assert blocked.json()["error"]["message"] == "Too many MFA attempts. Try again later."
    assert blocked.json()["error"]["details"]["retry_after_minutes"] == 15


async def test_dangerous_support_write_requires_recent_mfa(
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
    claims = decode_access_token(enrolled.access_token)
    stale_token = create_access_token(
        enrolled.user.id,
        tenant_id=None,
        is_developer=True,
        is_administrator=False,
        session_id=UUID(str(claims["sid"])),
        mfa_verified_at=utc_now() - timedelta(minutes=11),
    )
    headers = {"Authorization": f"Bearer {stale_token}"}

    readable = await auth_client.get("/api/v1/admin/tenants", headers=headers)
    blocked = await auth_client.post(
        "/api/v1/admin/tenants",
        headers=headers,
        json={
            "name": "Must not be created",
            "contact_email": "blocked-step-up@aurum.tj",
        },
    )

    assert readable.status_code == 200
    assert blocked.status_code == 403
    assert blocked.json()["error"]["details"] == {
        "reason": "mfa_step_up_required",
    }


async def test_support_mfa_flow_works_through_real_app_and_support_pools(
    client: AsyncClient,
    db_engine: AsyncEngine,
) -> None:
    """Prove SECURITY DEFINER grants and per-request pool selection end to end."""
    from app.core.db import app_engine, support_engine

    email = f"mfa-real-pools-{uuid4().hex}@aurum.tj"
    user_id: str | None = None
    code = "123456"
    salt = generate_code_salt()
    await app_engine.dispose()
    await support_engine.dispose()
    try:
        async with db_engine.begin() as connection:
            user_id = str(
                (
                    await connection.execute(
                        text(
                            "INSERT INTO public.app_user "
                            "(email, full_name, password_hash, is_developer, status) "
                            "VALUES (:email, 'MFA pool test', :password_hash, true, 'active') "
                            "RETURNING id"
                        ),
                        {
                            "email": email,
                            "password_hash": hash_password(_PASSWORD),
                        },
                    )
                ).scalar_one()
            )
            await connection.execute(
                text(
                    "INSERT INTO public.email_code "
                    "(email_lower, code_hash, code_salt, purpose, ip_address, expires_at) "
                    "VALUES (:email, :code_hash, :salt, 'login', '127.0.0.1', "
                    "now() + interval '10 minutes')"
                ),
                {
                    "email": email,
                    "code_hash": hash_code(code, salt),
                    "salt": salt,
                },
            )

        login = await client.post(
            "/api/v1/auth/login/verify",
            json={"email": email, "code": code, "password": _PASSWORD},
        )
        assert login.status_code == 200
        assert login.json()["status"] == "mfa_enrollment_required"
        challenge_token = str(login.json()["challenge_token"])

        enrollment = await client.post(
            "/api/v1/auth/mfa/enroll/start",
            json={"challenge_token": challenge_token},
        )
        assert enrollment.status_code == 200
        secret = str(enrollment.json()["secret"])
        confirmation = await client.post(
            "/api/v1/auth/mfa/enroll/confirm",
            json={
                "challenge_token": challenge_token,
                "code": _totp_code(secret, utc_now()),
            },
        )
        assert confirmation.status_code == 200

        me = await client.get(
            "/api/v1/auth/me",
            headers={
                "Authorization": f"Bearer {confirmation.json()['access_token']}",
            },
        )
        assert me.status_code == 200
        assert me.json()["email"] == email
        assert me.json()["is_developer"] is True
    finally:
        await app_engine.dispose()
        await support_engine.dispose()
        async with db_engine.begin() as connection:
            if user_id is not None:
                await connection.execute(
                    text(
                        "DELETE FROM public.audit_log "
                        "WHERE user_id = CAST(:user_id AS UUID) "
                        "OR record_id = CAST(:user_id AS UUID)"
                    ),
                    {"user_id": user_id},
                )
            await connection.execute(
                text("DELETE FROM public.login_attempt WHERE email_lower = :email"),
                {"email": email},
            )
            await connection.execute(
                text("DELETE FROM public.email_code WHERE email_lower = :email"),
                {"email": email},
            )
            if user_id is not None:
                await connection.execute(
                    text("DELETE FROM public.app_user WHERE id = CAST(:user_id AS UUID)"),
                    {"user_id": user_id},
                )
