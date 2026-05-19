"""GET /api/v1/auth/me."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token


async def test_me_without_token_returns_401(auth_client: AsyncClient) -> None:
    response = await auth_client.get("/api/v1/auth/me")
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "authentication_required"


async def test_me_with_valid_token_returns_user(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,
) -> None:
    user = await make_user(email="me@aurum.tj", full_name="Me User")
    token = create_access_token(
        user.id,
        tenant_id=user.home_tenant_id,
        is_developer=user.is_developer,
        is_administrator=user.is_administrator,
    )

    response = await auth_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "me@aurum.tj"
    assert body["full_name"] == "Me User"
    assert body["is_developer"] is False
    assert body["is_administrator"] is False


async def test_me_with_garbage_token_returns_401(auth_client: AsyncClient) -> None:
    response = await auth_client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not.a.real.jwt"}
    )
    assert response.status_code == 401
