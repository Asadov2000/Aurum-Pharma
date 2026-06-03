"""Login flow: request code → verify code → get tokens."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    AuthenticationError,
    NotFoundError,
    RateLimitError,
)
from app.domains.auth.models import EmailCode, LoginAttempt
from app.domains.auth.repository import AuthRepository
from app.domains.auth.service import AuthService


async def _latest_code_for(db_session: AsyncSession, email: str) -> EmailCode:
    """The verify_login_code path hashes against the stored row, so tests need
    the raw 6-digit code. We don't keep plaintext, so we monkey the service:
    issue a code, look up the row, and use the stored code_hash + salt
    indirectly by overriding `hash_code`. Simpler approach: re-issue a code
    and capture it via a known seam — see _make_code below."""
    stmt = (
        select(EmailCode)
        .where(EmailCode.email_lower == email.lower())
        .order_by(EmailCode.created_at.desc())
        .limit(1)
    )
    result = await db_session.execute(stmt)
    code = result.scalar_one_or_none()
    assert code is not None, f"no email_code row for {email}"
    return code


async def _seed_code(
    db_session: AsyncSession,
    email: str,
    *,
    code: str = "123456",
) -> EmailCode:
    """Insert a known plaintext code so verify can be tested deterministically."""
    from datetime import timedelta

    from app.core.security import generate_code_salt, hash_code
    from app.core.time import utc_now

    repo = AuthRepository(db_session)
    salt = generate_code_salt()
    ec = await repo.insert_email_code(
        email_lower=email.lower(),
        code_hash=hash_code(code, salt),
        code_salt=salt,
        purpose="login",
        ip_address="127.0.0.1",
        expires_at=utc_now() + timedelta(minutes=10),
    )
    return ec


# -----------------------------------------------------------------------------
# request_login_code
# -----------------------------------------------------------------------------


async def test_request_login_code_creates_row(db_session: AsyncSession, make_user) -> None:
    user = await make_user(email="alice@aurum.tj")
    service = AuthService(AuthRepository(db_session))

    await service.request_login_code(email=user.email, ip_address="127.0.0.1")

    code_row = await _latest_code_for(db_session, user.email)
    assert code_row.email_lower == "alice@aurum.tj"
    assert code_row.purpose == "login"
    assert code_row.used_at is None


async def test_request_login_code_works_for_unknown_email(
    db_session: AsyncSession,
) -> None:
    """Anti-enumeration: request returns 'ok' even if no user exists."""
    service = AuthService(AuthRepository(db_session))
    await service.request_login_code(email="nobody@aurum.tj", ip_address="127.0.0.1")
    # A code row is still inserted (caller can't tell from the outside).
    code_row = await _latest_code_for(db_session, "nobody@aurum.tj")
    assert code_row is not None


async def test_request_login_code_rate_limit_per_minute(
    db_session: AsyncSession,
) -> None:
    service = AuthService(AuthRepository(db_session))
    await service.request_login_code(email="rl@aurum.tj", ip_address="127.0.0.1")
    with pytest.raises(RateLimitError):
        await service.request_login_code(email="rl@aurum.tj", ip_address="127.0.0.1")


# -----------------------------------------------------------------------------
# verify_login_code
# -----------------------------------------------------------------------------


async def test_verify_login_code_happy_path(db_session: AsyncSession, make_user) -> None:
    user = await make_user(email="bob@aurum.tj")
    await _seed_code(db_session, user.email, code="123456")
    service = AuthService(AuthRepository(db_session))

    access, refresh, expires_in = await service.verify_login_code(
        email=user.email,
        code="123456",
        password=None,
        ip_address="127.0.0.1",
    )

    assert access  # non-empty JWT
    assert len(refresh) == 64  # 32 bytes hex
    assert expires_in == 15 * 60


async def test_verify_invalid_code(db_session: AsyncSession, make_user) -> None:
    user = await make_user(email="cat@aurum.tj")
    await _seed_code(db_session, user.email, code="123456")
    service = AuthService(AuthRepository(db_session))

    with pytest.raises(AuthenticationError):
        await service.verify_login_code(
            email=user.email,
            code="999999",
            password=None,
            ip_address="127.0.0.1",
        )


async def test_verify_unknown_user(db_session: AsyncSession) -> None:
    """Code exists but no user — caller learns the user doesn't exist."""
    await _seed_code(db_session, "ghost@aurum.tj", code="555555")
    service = AuthService(AuthRepository(db_session))

    with pytest.raises(NotFoundError):
        await service.verify_login_code(
            email="ghost@aurum.tj",
            code="555555",
            password=None,
            ip_address="127.0.0.1",
        )


async def test_verify_five_failures_then_blocked(db_session: AsyncSession, make_user) -> None:
    user = await make_user(email="brute@aurum.tj")
    service = AuthService(AuthRepository(db_session))

    # 5 bad codes (each leaves a code_failed attempt)
    for _ in range(5):
        await _seed_code(db_session, user.email, code="111111")
        with pytest.raises(AuthenticationError):
            await service.verify_login_code(
                email=user.email,
                code="000000",
                password=None,
                ip_address="127.0.0.1",
            )

    # The 6th attempt — even with the correct code — gets RateLimitError
    await _seed_code(db_session, user.email, code="123456")
    with pytest.raises(RateLimitError):
        await service.verify_login_code(
            email=user.email,
            code="123456",
            password=None,
            ip_address="127.0.0.1",
        )

    # And a 'blocked' attempt is recorded.
    stmt = select(LoginAttempt).where(
        LoginAttempt.email_lower == "brute@aurum.tj",
        LoginAttempt.outcome == "blocked",
    )
    result = await db_session.execute(stmt)
    blocked_rows = result.scalars().all()
    assert len(blocked_rows) >= 1


async def test_verify_password_required_and_correct(db_session: AsyncSession, make_user) -> None:
    user = await make_user(email="pwd@aurum.tj", password="hunter2")
    await _seed_code(db_session, user.email, code="123456")
    service = AuthService(AuthRepository(db_session))

    access, refresh, _ = await service.verify_login_code(
        email=user.email,
        code="123456",
        password="hunter2",
        ip_address="127.0.0.1",
    )
    assert access and refresh


async def test_verify_password_required_but_wrong(db_session: AsyncSession, make_user) -> None:
    user = await make_user(email="pwd2@aurum.tj", password="hunter2")
    await _seed_code(db_session, user.email, code="123456")
    service = AuthService(AuthRepository(db_session))

    with pytest.raises(AuthenticationError):
        await service.verify_login_code(
            email=user.email,
            code="123456",
            password="wrong",
            ip_address="127.0.0.1",
        )


# -----------------------------------------------------------------------------
# HTTP endpoint smoke (proves the wiring)
# -----------------------------------------------------------------------------


async def test_login_code_endpoint_returns_202(auth_client: AsyncClient) -> None:
    response = await auth_client.post(
        "/api/v1/auth/login/code", json={"email": "via-http@aurum.tj"}
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "ok"
    # In development the endpoint also returns dev_code (UI prefill); it must
    # never be a real-prod concern, but here we just assert the field exists.
    assert "dev_code" in body
