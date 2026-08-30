"""Security-boundary checks for ownership-transfer route wiring and visibility."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi.routing import APIRoute
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.deps import (
    current_user,
    get_db,
    require_recent_account_mfa,
    require_recent_owner_mfa,
)
from app.core.security import create_access_token
from app.core.time import utc_now
from app.domains.roles.router import router
from app.main import app


def _route(path: str, method: str) -> APIRoute:
    return next(
        route
        for route in router.routes
        if isinstance(route, APIRoute) and route.path == path and method in (route.methods or set())
    )


def _direct_dependencies(route: APIRoute) -> set[object]:
    return {dependency.call for dependency in route.dependant.dependencies}


def test_ownership_transfer_routes_keep_role_specific_mfa_boundaries() -> None:
    listing = _direct_dependencies(_route("/api/v1/ownership-transfers", "GET"))
    creation = _direct_dependencies(_route("/api/v1/ownership-transfers", "POST"))
    cancellation = _direct_dependencies(
        _route("/api/v1/ownership-transfers/{request_id}/cancel", "POST")
    )
    acceptance = _direct_dependencies(
        _route("/api/v1/ownership-transfers/{request_id}/accept", "POST")
    )

    assert current_user in listing
    assert require_recent_owner_mfa in creation
    assert require_recent_owner_mfa in cancellation
    assert require_recent_account_mfa in acceptance
    assert require_recent_owner_mfa not in acceptance


@pytest_asyncio.fixture
# pytest-asyncio's decorator does not preserve this async-generator fixture type for mypy.
async def ownership_history_scenario(  # type: ignore[no-untyped-def]
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
    client: AsyncClient,
):
    tenant_id = uuid4()
    foreign_tenant_id = uuid4()
    transfer_id = uuid4()
    initiator_user_id = uuid4()
    target_user_id = uuid4()
    outsider_user_id = uuid4()
    foreign_user_id = uuid4()
    initiator_membership_id = uuid4()
    target_membership_id = uuid4()
    outsider_membership_id = uuid4()
    foreign_membership_id = uuid4()
    now = utc_now()

    async with maintenance_engine.begin() as connection:
        await connection.execute(
            text("""
                INSERT INTO public.tenant (id, name, contact_email, status)
                VALUES
                  (:tenant_id, :tenant_name, :tenant_email, 'active'),
                  (:foreign_tenant_id, :foreign_name, :foreign_email, 'active')
                """),
            {
                "tenant_id": tenant_id,
                "tenant_name": f"Transfer tenant {tenant_id}",
                "tenant_email": f"transfer-{tenant_id}@aurum.test",
                "foreign_tenant_id": foreign_tenant_id,
                "foreign_name": f"Foreign tenant {foreign_tenant_id}",
                "foreign_email": f"foreign-{foreign_tenant_id}@aurum.test",
            },
        )
        await connection.execute(
            text("""
                INSERT INTO public.app_user (
                  id, email, full_name, home_tenant_id, status, activated_at
                ) VALUES
                  (:initiator_id, :initiator_email, 'Initiator', :tenant_id, 'active', :now),
                  (:target_id, :target_email, 'Target', :tenant_id, 'active', :now),
                  (:outsider_id, :outsider_email, 'Outsider', :tenant_id, 'active', :now),
                  (:foreign_id, :foreign_email, 'Foreign user',
                   :foreign_tenant_id, 'active', :now)
                """),
            {
                "initiator_id": initiator_user_id,
                "initiator_email": f"initiator-{initiator_user_id}@aurum.test",
                "target_id": target_user_id,
                "target_email": f"target-{target_user_id}@aurum.test",
                "outsider_id": outsider_user_id,
                "outsider_email": f"outsider-{outsider_user_id}@aurum.test",
                "foreign_id": foreign_user_id,
                "foreign_email": f"foreign-{foreign_user_id}@aurum.test",
                "tenant_id": tenant_id,
                "foreign_tenant_id": foreign_tenant_id,
                "now": now,
            },
        )
        await connection.execute(
            text("""
                INSERT INTO public.tenant_membership (
                  id, tenant_id, user_id, full_name, status, activated_at
                ) VALUES
                  (:initiator_membership_id, :tenant_id, :initiator_id,
                   'Initiator', 'active', :now),
                  (:target_membership_id, :tenant_id, :target_id,
                   'Target', 'active', :now),
                  (:outsider_membership_id, :tenant_id, :outsider_id,
                   'Outsider', 'active', :now),
                  (:foreign_membership_id, :foreign_tenant_id, :foreign_id,
                   'Foreign user', 'active', :now)
                """),
            {
                "initiator_membership_id": initiator_membership_id,
                "target_membership_id": target_membership_id,
                "outsider_membership_id": outsider_membership_id,
                "foreign_membership_id": foreign_membership_id,
                "tenant_id": tenant_id,
                "foreign_tenant_id": foreign_tenant_id,
                "initiator_id": initiator_user_id,
                "target_id": target_user_id,
                "outsider_id": outsider_user_id,
                "foreign_id": foreign_user_id,
                "now": now,
            },
        )
        await connection.execute(
            text("SELECT set_config('app.user_id', :user_id, true)"),
            {"user_id": str(initiator_user_id)},
        )
        await connection.execute(
            text("""
                INSERT INTO public.tenant_ownership_transfer (
                  id, tenant_id, initiator_membership_id, target_membership_id,
                  status, expires_at, created_by, updated_by
                ) VALUES (
                  :transfer_id, :tenant_id, :initiator_membership_id,
                  :target_membership_id, 'pending', :expires_at,
                  :initiator_id, :initiator_id
                )
                """),
            {
                "transfer_id": transfer_id,
                "tenant_id": tenant_id,
                "initiator_membership_id": initiator_membership_id,
                "target_membership_id": target_membership_id,
                "expires_at": now + timedelta(days=3),
                "initiator_id": initiator_user_id,
            },
        )

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _request_history(user_id: UUID, request_tenant_id: UUID) -> Response:
        await db_session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(request_tenant_id)},
        )
        token = create_access_token(
            user_id,
            tenant_id=request_tenant_id,
            is_developer=False,
            is_administrator=False,
        )
        return await client.get(
            "/api/v1/ownership-transfers",
            headers={"Authorization": f"Bearer {token}"},
        )

    app.dependency_overrides[get_db] = _override_db
    try:
        yield {
            "tenant_id": tenant_id,
            "foreign_tenant_id": foreign_tenant_id,
            "transfer_id": transfer_id,
            "initiator_user_id": initiator_user_id,
            "target_user_id": target_user_id,
            "outsider_user_id": outsider_user_id,
            "foreign_user_id": foreign_user_id,
        }, _request_history
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with maintenance_engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM public.tenant_ownership_transfer " "WHERE id = :transfer_id"),
                {"transfer_id": transfer_id},
            )
            await connection.execute(
                text("DELETE FROM public.tenant WHERE id IN (:tenant_id, :foreign_tenant_id)"),
                {"tenant_id": tenant_id, "foreign_tenant_id": foreign_tenant_id},
            )
            await connection.execute(
                text(
                    "DELETE FROM public.audit_log "
                    "WHERE tenant_id IN (:tenant_id, :foreign_tenant_id)"
                ),
                {"tenant_id": tenant_id, "foreign_tenant_id": foreign_tenant_id},
            )
            await connection.execute(
                text(
                    "DELETE FROM public.app_user WHERE id IN ("
                    ":initiator_id, :target_id, :outsider_id, :foreign_id)"
                ),
                {
                    "initiator_id": initiator_user_id,
                    "target_id": target_user_id,
                    "outsider_id": outsider_user_id,
                    "foreign_id": foreign_user_id,
                },
            )


@pytest.mark.parametrize(
    ("actor_key", "tenant_key", "can_see_transfer"),
    [
        ("initiator_user_id", "tenant_id", True),
        ("target_user_id", "tenant_id", True),
        ("outsider_user_id", "tenant_id", False),
        ("foreign_user_id", "foreign_tenant_id", False),
    ],
    ids=["initiator", "target", "same-tenant-outsider", "foreign-tenant"],
)
async def test_ownership_transfer_history_is_visible_only_to_participants(
    ownership_history_scenario,
    actor_key: str,
    tenant_key: str,
    can_see_transfer: bool,
) -> None:
    scenario, request_history = ownership_history_scenario

    response = await request_history(scenario[actor_key], scenario[tenant_key])

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    expected_items = [{"id": str(scenario["transfer_id"])}] if can_see_transfer else []
    assert [{"id": item["id"]} for item in response.json()["items"]] == expected_items
