"""Role templates: the global recommendation library.

Covers: the two seeded presets carry exactly the current owner/seller sets,
GET /templates is gated by roles.create (seller refused, owner allowed), and a
template cannot be used to dodge the create_role anti-escalation subset rule.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.errors import PermissionDeniedError
from app.core.security import create_access_token
from app.domains.auth.models import AppUser
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.roles.models import UserAssignment
from app.domains.roles.repository import RolesRepository
from app.domains.roles.service import RolesService
from app.main import app


async def test_templates_match_current_owner_and_seller_sets(
    db_session: AsyncSession, system_roles
) -> None:
    service = RolesService(RolesRepository(db_session))
    by_name = {t.name: codes for t, codes in await service.list_templates_with_permissions()}

    assert "Владелец" in by_name
    assert "Кассир" in by_name

    owner_codes = sorted(await service.repo.get_role_permissions(system_roles["owner"].id))
    seller_codes = sorted(await service.repo.get_role_permissions(system_roles["seller"].id))

    assert sorted(by_name["Владелец"]) == owner_codes
    assert sorted(by_name["Кассир"]) == seller_codes


async def test_templates_endpoint_gated_by_roles_create(
    db_session: AsyncSession, client: AsyncClient, system_roles
) -> None:
    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        foundation = FoundationService(FoundationRepository(db_session))
        nick = uuid4().hex[:8]
        tenant = await foundation.create_tenant(
            payload={"name": f"Tmpl {nick}", "contact_email": f"t-{nick}@aurum.tj"}
        )
        await foundation.update_tenant(tenant.id, fields={"status": "active"})

        seller = AppUser(
            email=f"seller-{nick}@aurum.tj",
            full_name="Seller",
            home_tenant_id=tenant.id,
            status="active",
        )
        owner = AppUser(
            email=f"owner-{nick}@aurum.tj",
            full_name="Owner",
            home_tenant_id=tenant.id,
            status="active",
        )
        db_session.add_all([seller, owner])
        await db_session.flush()
        await db_session.refresh(seller)
        await db_session.refresh(owner)

        # The owner system role carries roles.create (migration 0018).
        db_session.add(
            UserAssignment(
                user_id=owner.id,
                tenant_id=tenant.id,
                role_id=system_roles["owner"].id,
                is_active=True,
            )
        )
        await db_session.flush()

        seller_token = create_access_token(
            seller.id, tenant_id=tenant.id, is_developer=False, is_administrator=False
        )
        owner_token = create_access_token(
            owner.id, tenant_id=tenant.id, is_developer=False, is_administrator=False
        )

        seller_resp = await client.get(
            "/api/v1/templates", headers={"Authorization": f"Bearer {seller_token}"}
        )
        assert seller_resp.status_code == 403

        owner_resp = await client.get(
            "/api/v1/templates", headers={"Authorization": f"Bearer {owner_token}"}
        )
        assert owner_resp.status_code == 200
        names = {t["name"] for t in owner_resp.json()}
        assert {"Владелец", "Кассир"} <= names
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_template_cannot_bypass_anti_escalation(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    """A preset is only a hint: building a role from the rich «Владелец» set as
    an actor who lacks those permissions still hits the 403 subset guard."""
    service = RolesService(RolesRepository(db_session))
    templates = {t.name: codes for t, codes in await service.list_templates_with_permissions()}
    owner_template = templates["Владелец"]

    tenant = await make_tenant()
    actor = await make_user(email="weak-tmpl@aurum.tj", home_tenant_id=tenant.id)

    with pytest.raises(PermissionDeniedError):
        await service.create_role(
            actor_level=3,
            actor_id=actor.id,
            actor_permissions={"pos.sell"},  # holds almost nothing
            actor_is_support=False,
            tenant_id=tenant.id,
            name="Из шаблона",
            description=None,
            level=4,
            permission_codes=owner_template,
        )
