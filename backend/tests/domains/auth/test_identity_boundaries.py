"""Adversarial tests for contact ownership and recovery purpose isolation."""

from __future__ import annotations

from typing import Protocol

import pytest
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ServiceUnavailableError
from app.domains.auth.models import AppUser
from tests.domains.auth.test_identities import BASE, PASSWORD, PHONE, _verified

# Re-export the maintenance-owned pytest fixture for protected-table assertions.
from tests.domains.auth.test_identities import db_engine as db_engine  # noqa: PLC0414
from tests.domains.auth.test_login import _seed_code
from tests.domains.auth.test_optional_mfa import _actor
from tests.domains.auth.test_sessions import _auth_headers, _create_session


class UserFactory(Protocol):
    async def __call__(self, *, password: str | None = None) -> AppUser: ...


@pytest.fixture(autouse=True)
def simulated_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "SMS_PROVIDER", "development")
    monkeypatch.setattr("app.domains.auth.identity_service.generate_email_code", lambda: "123456")


async def test_contact_from_another_account_cannot_be_claimed(
    auth_client: AsyncClient, db_session: AsyncSession, make_user: UserFactory
) -> None:
    owner = await make_user(password=PASSWORD)
    await _verified(db_session, owner.id)
    requester = await make_user(password=PASSWORD)
    current = await _create_session(db_session, user=requester)
    headers = _auth_headers(requester, current)
    await _actor(db_session, headers["Authorization"].removeprefix("Bearer "))
    sent = await auth_client.post(
        f"{BASE}/phone/code", headers=headers, json={"phone": PHONE, "password": PASSWORD}
    )
    assert sent.status_code == 202
    denied = await auth_client.post(
        f"{BASE}/phone/verify", headers=headers, json={"phone": PHONE, "code": "123456"}
    )
    assert denied.status_code == 401
    assert (
        await db_session.scalar(
            text("SELECT id FROM app_user WHERE verified_phone=:phone"), {"phone": PHONE}
        )
        == owner.id
    )
    assert (
        await db_session.scalar(
            text("SELECT verified_phone FROM app_user WHERE id=:id"), {"id": requester.id}
        )
        is None
    )


@pytest.mark.parametrize("change", ["other_session", "revoked_session"])
async def test_phone_confirmation_stays_bound_to_originating_live_session(
    auth_client: AsyncClient, db_session: AsyncSession, make_user: UserFactory, change: str
) -> None:
    user = await make_user(password=PASSWORD)
    current = await _create_session(db_session, user=user)
    other = await _create_session(db_session, user=user)
    headers = _auth_headers(user, current)
    await _actor(db_session, headers["Authorization"].removeprefix("Bearer "))
    sent = await auth_client.post(
        f"{BASE}/phone/code", headers=headers, json={"phone": PHONE, "password": PASSWORD}
    )
    assert sent.status_code == 202
    if change == "other_session":
        headers = _auth_headers(user, other)
        await _actor(db_session, headers["Authorization"].removeprefix("Bearer "))
    else:
        await db_session.execute(
            text("UPDATE session SET revoked_at=now() WHERE id=:id"), {"id": current.id}
        )
    rejection_savepoint = await db_session.begin_nested()
    rejected = await auth_client.post(
        f"{BASE}/phone/verify", headers=headers, json={"phone": PHONE, "code": "123456"}
    )
    assert rejected.status_code == 401
    # Inactive-session SQL protection aborts its transaction. Production owns a
    # request transaction; this fixture deliberately shares one across calls.
    await rejection_savepoint.rollback()
    assert (
        await db_session.scalar(
            text("SELECT verified_phone FROM app_user WHERE id=:id"), {"id": user.id}
        )
        is None
    )


async def test_sms_login_code_cannot_reset_password(
    auth_client: AsyncClient, db_session: AsyncSession, make_user: UserFactory
) -> None:
    user = await make_user(password=PASSWORD)
    await _verified(db_session, user.id)
    sent = await auth_client.post(f"{BASE}/login/phone/code", json={"phone": PHONE})
    assert sent.status_code == 202
    rejected = await auth_client.post(
        f"{BASE}/password/reset",
        json={
            "channel": "phone",
            "contact": PHONE,
            "code": "123456",
            "new_password": "Other-password-42!",
        },
    )
    assert rejected.status_code == 401
    login = await auth_client.post(
        f"{BASE}/login/password", json={"email": user.email, "password": PASSWORD}
    )
    assert login.status_code == 200


async def test_expired_sms_cannot_authenticate(
    auth_client: AsyncClient, db_session: AsyncSession, make_user: UserFactory
) -> None:
    user = await make_user(password=PASSWORD)
    await _verified(db_session, user.id)
    await auth_client.post(f"{BASE}/login/phone/code", json={"phone": PHONE})
    await db_session.execute(
        text(
            "UPDATE auth_identity_code SET expires_at=now()-INTERVAL '1 second' "
            "WHERE contact=:phone"
        ),
        {"phone": PHONE},
    )
    result = await auth_client.post(
        f"{BASE}/login/phone/verify", json={"phone": PHONE, "code": "123456"}
    )
    assert result.status_code == 401


async def test_unverified_email_cannot_recover_an_existing_password(
    auth_client: AsyncClient, make_user: UserFactory
) -> None:
    user = await make_user(password=PASSWORD)
    sent = await auth_client.post(
        f"{BASE}/password/reset/code", json={"channel": "email", "contact": user.email}
    )
    assert sent.status_code == 202
    result = await auth_client.post(
        f"{BASE}/password/reset",
        json={
            "channel": "email",
            "contact": user.email,
            "code": "123456",
            "new_password": "Other-password-42!",
        },
    )
    assert result.status_code == 401


async def test_live_payom_never_exposes_sms_code_in_development_response(
    auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An unknown contact still returns the same response, without a real send.
    monkeypatch.setattr(get_settings(), "SMS_PROVIDER", "payom")
    result = await auth_client.post(f"{BASE}/login/phone/code", json={"phone": PHONE})
    assert result.status_code == 202
    assert result.json()["dev_code"] is None


async def test_rate_limit_is_shared_across_request_purposes(
    auth_client: AsyncClient, db_session: AsyncSession, make_user: UserFactory
) -> None:
    user = await make_user()
    await _verified(db_session, user.id)
    first = await auth_client.post(f"{BASE}/login/phone/code", json={"phone": PHONE})
    assert first.status_code == 202
    second = await auth_client.post(
        f"{BASE}/password/reset/code", json={"channel": "phone", "contact": PHONE}
    )
    assert second.status_code == 429


async def test_unknown_numbers_do_not_exhaust_paid_sms_budget(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user: UserFactory,
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "SMS_DAILY_LIMIT", 1)
    first = await auth_client.post(f"{BASE}/login/phone/code", json={"phone": PHONE})
    assert first.status_code == 202
    assert await redis.get("auth:identity:sms:daily") is None
    user = await make_user()
    await _verified(db_session, user.id)
    await db_session.execute(
        text(
            "UPDATE auth_identity_code SET created_at=now()-INTERVAL '61 seconds' "
            "WHERE contact=:phone"
        ),
        {"phone": PHONE},
    )
    sent = await auth_client.post(f"{BASE}/login/phone/code", json={"phone": PHONE})
    assert sent.status_code == 202
    assert int(await redis.get("auth:identity:sms:daily")) == 1


@pytest.mark.parametrize("purpose", ["login", "reset"])
@pytest.mark.parametrize("failure", ["timeout", "provider_rejection", "daily_budget"])
async def test_public_sms_delivery_failures_do_not_identify_accounts(
    purpose: str,
    failure: str,
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user: UserFactory,
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "SMS_PROVIDER", "payom")
    monkeypatch.setattr(get_settings(), "SMS_DAILY_LIMIT", 1)
    if failure == "daily_budget":
        await redis.set("auth:identity:sms:daily", 1, ex=86400)
    user = await make_user()
    await _verified(db_session, user.id)
    sent: list[str] = []

    async def unavailable(phone: str, code: str) -> None:
        sent.append(phone)
        assert code == "123456"
        raise ServiceUnavailableError(
            "SMS provider is temporarily unavailable"
            if failure == "timeout"
            else "SMS provider did not accept the request"
        )

    monkeypatch.setattr("app.domains.auth.identity_service.send_sms_code", unavailable)
    results = []
    for phone in (PHONE, "+992900000002"):
        path = "/login/phone/code" if purpose == "login" else "/password/reset/code"
        payload = {"phone": phone} if purpose == "login" else {"channel": "phone", "contact": phone}
        results.append(await auth_client.post(f"{BASE}{path}", json=payload))
    assert [result.status_code for result in results] == [202, 202]
    assert results[0].json() == results[1].json() == {"status": "ok", "dev_code": None}
    assert sent == ([] if failure == "daily_budget" else [PHONE])
    assert int(await redis.get("auth:identity:sms:daily")) == (
        2 if failure == "daily_budget" else 1
    )
    # An uncertain provider outcome must not discard a potentially delivered code.
    assert (
        await db_session.scalar(
            text(
                "SELECT count(*) FROM auth_identity_code WHERE contact=:phone AND used_at IS NULL"
            ),
            {"phone": PHONE},
        )
        == 1
    )


async def test_authenticated_phone_binding_still_reports_delivery_failure(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user: UserFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await make_user(password=PASSWORD)
    current = await _create_session(db_session, user=user)
    headers = _auth_headers(user, current)
    await _actor(db_session, headers["Authorization"].removeprefix("Bearer "))

    async def unavailable(phone: str, code: str) -> None:
        raise ServiceUnavailableError("SMS provider is temporarily unavailable")

    monkeypatch.setattr("app.domains.auth.identity_service.send_sms_code", unavailable)
    result = await auth_client.post(
        f"{BASE}/phone/code", headers=headers, json={"phone": PHONE, "password": PASSWORD}
    )
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "service_unavailable"


async def test_invalidating_unused_email_code_does_not_verify_contact(
    db_session: AsyncSession, make_user: UserFactory
) -> None:
    user = await make_user()
    await _seed_code(db_session, user.email)
    # This is the operation performed by account lifecycle/session revocation.
    await db_session.execute(
        text("UPDATE email_code SET used_at=now() WHERE email_lower=:email"), {"email": user.email}
    )
    assert (
        await db_session.scalar(
            text("SELECT email_verified_at FROM app_user WHERE id=:id"), {"id": user.id}
        )
        is None
    )
