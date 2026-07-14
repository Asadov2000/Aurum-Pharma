"""Login flow: request code → verify code → get tokens."""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

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

    salt = generate_code_salt()
    ec = EmailCode(
        email_lower=email.lower(),
        code_hash=hash_code(code, salt),
        code_salt=salt,
        purpose="login",
        ip_address="127.0.0.1",
        expires_at=utc_now() + timedelta(minutes=10),
    )
    db_session.add(ec)
    await db_session.flush()
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
    # In development the endpoint returns dev_code (UI prefill) — a real 6-digit
    # code. The production gate below proves it is withheld outside development.
    assert body["dev_code"] is not None
    assert len(body["dev_code"]) == 6 and body["dev_code"].isdigit()


async def test_login_code_endpoint_hides_code_in_production(
    auth_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returning the login code in the HTTP body would let any caller bypass
    e-mail delivery entirely. Outside development the endpoint must withhold it —
    the code goes only through the normal channel."""
    import sys
    from types import SimpleNamespace

    # Reach the module via sys.modules: the auth package re-exports `router`
    # (the APIRouter), so attribute access app.domains.auth.router would hit the
    # router object, not this module.
    router_module = sys.modules["app.domains.auth.router"]

    for env in ("production", "staging"):
        monkeypatch.setattr(
            router_module, "get_settings", lambda env=env: SimpleNamespace(ENVIRONMENT=env)
        )
        response = await auth_client.post(
            "/api/v1/auth/login/code", json={"email": f"prod-{env}@aurum.tj"}
        )
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "ok"
        assert body["dev_code"] is None, f"dev_code leaked in {env}"


async def test_failed_http_logins_persist_and_trigger_lockout(
    client: AsyncClient,
    db_engine: AsyncEngine,
) -> None:
    """Exercise the real auth DB dependency rather than the support override."""
    from app.core.db import app_engine

    email = f"http-lockout-{uuid4().hex}@aurum.tj"
    await app_engine.dispose()
    try:
        issued = await client.post("/api/v1/auth/login/code", json={"email": email})
        assert issued.status_code == 202
        dev_code = issued.json()["dev_code"]
        wrong_code = "000000" if dev_code != "000000" else "999999"

        for _ in range(5):
            rejected = await client.post(
                "/api/v1/auth/login/verify",
                json={"email": email, "code": wrong_code},
            )
            assert rejected.status_code == 401

        blocked = await client.post(
            "/api/v1/auth/login/verify",
            json={"email": email, "code": dev_code},
        )
        assert blocked.status_code == 429

        async with db_engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT outcome, count(*) AS total "
                        "FROM public.login_attempt "
                        "WHERE email_lower = :email "
                        "GROUP BY outcome"
                    ),
                    {"email": email},
                )
            ).mappings()
            outcomes = {row["outcome"]: row["total"] for row in rows}

        assert outcomes == {"blocked": 1, "code_failed": 5, "code_requested": 1}
    finally:
        async with db_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM public.login_attempt WHERE email_lower = :email"),
                {"email": email},
            )
            await conn.execute(
                text("DELETE FROM public.email_code WHERE email_lower = :email"),
                {"email": email},
            )
        await app_engine.dispose()


async def test_login_verify_sets_httponly_refresh_cookie(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,
) -> None:
    user = await make_user(email="cookie-login@aurum.tj")
    await _seed_code(db_session, user.email, code="123456")

    response = await auth_client.post(
        "/api/v1/auth/login/verify",
        json={"email": user.email, "code": "123456"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"] is None
    set_cookie = response.headers["set-cookie"]
    assert "aurum_refresh_token=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Path=/api/v1/auth" in set_cookie
    assert "SameSite=lax" in set_cookie


async def test_refresh_endpoint_rotates_cookie_session(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,
) -> None:
    user = await make_user(email="cookie-refresh@aurum.tj")
    await _seed_code(db_session, user.email, code="123456")
    login = await auth_client.post(
        "/api/v1/auth/login/verify",
        json={"email": user.email, "code": "123456"},
    )
    assert login.status_code == 200

    response = await auth_client.post(
        "/api/v1/auth/refresh",
        headers={"Origin": "http://localhost:5173"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"] is None
    assert "aurum_refresh_token=" in response.headers["set-cookie"]


async def test_refresh_endpoint_blocks_untrusted_origin_for_cookie_session(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,
) -> None:
    user = await make_user(email="cookie-origin@aurum.tj")
    await _seed_code(db_session, user.email, code="123456")
    login = await auth_client.post(
        "/api/v1/auth/login/verify",
        json={"email": user.email, "code": "123456"},
    )
    assert login.status_code == 200

    response = await auth_client.post(
        "/api/v1/auth/refresh",
        headers={"Origin": "https://evil.example"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"
