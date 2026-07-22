"""Support database selection must remain limited to support-only routes."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.middleware.auth_context import AuthContextMiddleware

SUPPORT_USER_ID = UUID("11111111-1111-4111-8111-111111111111")
AUTH_SESSION_ID = UUID("22222222-2222-4222-8222-222222222222")
SUPPORT_ACCESS_ID = UUID("33333333-3333-4333-8333-333333333333")


@pytest.mark.parametrize(
    ("path", "claims", "expected"),
    [
        (
            "/api/v1/admin/tenants",
            {"sub": "developer", "is_developer": True},
            True,
        ),
        (
            "/api/v1/admin/tenants",
            {"sub": "administrator", "is_administrator": True},
            True,
        ),
        (
            "/api/v1/admin/audit/global",
            {"sub": "developer", "is_developer": True},
            True,
        ),
        (
            "/api/v1/roles",
            {"sub": "developer", "is_developer": True},
            False,
        ),
        (
            "/api/v1/administer",
            {"sub": "developer", "is_developer": True},
            False,
        ),
        (
            "/api/v1/admin/tenants",
            {"sub": "tenant-owner"},
            False,
        ),
    ],
)
async def test_support_pool_is_limited_to_admin_routes(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    claims: Mapping[str, object],
    expected: bool,
) -> None:
    app = FastAPI()
    app.add_middleware(AuthContextMiddleware)

    monkeypatch.setattr(
        "app.middleware.auth_context.decode_token",
        lambda _token: dict(claims),
    )

    @app.get("/{path:path}")
    async def inspect_context(request: Request) -> dict[str, bool]:
        return {
            "use_support_pool": bool(getattr(request.state, "use_support_pool", False)),
            "is_support_session": bool(getattr(request.state, "is_support_session", False)),
        }

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(path, headers={"Authorization": "Bearer signed"})

    assert response.status_code == 200
    assert response.json() == {
        "use_support_pool": expected,
        "is_support_session": expected,
    }


@pytest.mark.parametrize(
    ("method", "path", "expected_support_access"),
    [
        ("GET", "/api/v1/auth/me", True),
        ("GET", "/api/v1/roles", True),
        ("PATCH", "/api/v1/roles/44444444-4444-4444-8444-444444444444", True),
        ("GET", "/api/v1/users", True),
        ("DELETE", "/api/v1/users/44444444-4444-4444-8444-444444444444", True),
        ("GET", "/api/v1/branches", True),
        ("POST", "/api/v1/branches", False),
        ("GET", "/api/v1/tenant/settings", False),
        ("GET", "/api/v1/catalog/products", False),
        ("GET", "/api/v1/admin/tenants", False),
    ],
)
async def test_scoped_support_header_is_accepted_only_by_explicit_routes(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    expected_support_access: bool,
) -> None:
    app = FastAPI()
    app.add_middleware(AuthContextMiddleware)

    monkeypatch.setattr(
        "app.middleware.auth_context.decode_token",
        lambda _token: {
            "sub": str(SUPPORT_USER_ID),
            "sid": str(AUTH_SESSION_ID),
            "is_administrator": True,
        },
    )

    @app.api_route("/{path:path}", methods=["GET", "POST", "PATCH", "DELETE"])
    async def inspect_context(request: Request) -> dict[str, str | bool | None]:
        support_session_id = getattr(request.state, "support_access_session_id", None)
        return {
            "support_access_session_id": (
                str(support_session_id) if support_session_id is not None else None
            ),
            "use_support_pool": bool(getattr(request.state, "use_support_pool", False)),
        }

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.request(
            method,
            path,
            headers={
                "Authorization": "Bearer signed",
                "X-Aurum-Support-Session": str(SUPPORT_ACCESS_ID),
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "support_access_session_id": (str(SUPPORT_ACCESS_ID) if expected_support_access else None),
        "use_support_pool": path.startswith("/api/v1/admin/"),
    }
