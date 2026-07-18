"""Support database selection must remain limited to support-only routes."""

from __future__ import annotations

from collections.abc import Mapping

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.middleware.auth_context import AuthContextMiddleware


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
