"""list_users — pagination + batched assignments (no per-user N+1)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.roles.repository import RolesRepository
from app.domains.roles.service import RolesService


async def _invite(service: RolesService, *, tenant_id, role_id, email, name):  # type: ignore[no-untyped-def]
    await service.invite_user(
        actor_level=2,  # administrator
        actor_id=None,
        tenant_id=tenant_id,
        email=email,
        full_name=name,
        role_id=role_id,
        branch_id=None,
        password_required=False,
    )


async def test_list_users_paginates_and_batches_assignments(
    db_session: AsyncSession, make_tenant, make_tenant_role
) -> None:
    tenant = await make_tenant()
    seller = await make_tenant_role(tenant_id=tenant.id, template_name="Кассир", level=4)
    service = RolesService(RolesRepository(db_session))

    for i in range(3):
        await _invite(
            service,
            tenant_id=tenant.id,
            role_id=seller.id,
            email=f"u{i}@list.aurum.tj",
            name=f"User {i}",
        )

    page1, total = await service.list_users(tenant.id, page=1, page_size=2)
    assert total == 3
    assert len(page1) == 2
    # Every row carries its (batched) assignments — pointing at the seller role.
    for _user, assignments in page1:
        assert len(assignments) == 1
        assert assignments[0].role_id == seller.id

    page2, total2 = await service.list_users(tenant.id, page=2, page_size=2)
    assert total2 == 3
    assert len(page2) == 1


async def test_list_users_empty_tenant(db_session: AsyncSession, make_tenant) -> None:
    tenant = await make_tenant()
    service = RolesService(RolesRepository(db_session))
    pairs, total = await service.list_users(tenant.id, page=1, page_size=50)
    assert pairs == []
    assert total == 0
