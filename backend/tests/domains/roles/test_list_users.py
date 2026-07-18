"""list_users — pagination + batched assignments (no per-user N+1)."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.roles.repository import RolesRepository
from app.domains.roles.service import RolesService


async def _create_attached_user(
    service: RolesService,
    *,
    actor_id: UUID,
    tenant_id: UUID,
    role_id: UUID,
    email: str,
    name: str,
) -> None:
    account, _membership = await service.create_tenant_account(
        tenant_id=tenant_id,
        email=email,
        full_name=name,
    )
    await service.assign_role(
        actor_id=actor_id,
        actor_permissions=set(),
        actor_permission_scopes={},
        actor_is_developer=True,
        actor_is_administrator=False,
        tenant_id=tenant_id,
        target_user_id=account.id,
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
    actor_id = uuid4()

    for i in range(3):
        await _create_attached_user(
            service,
            actor_id=actor_id,
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


async def test_list_roles_filters_to_current_tenant(
    db_session: AsyncSession, make_tenant, make_tenant_role, make_owner
) -> None:
    tenant_a = await make_tenant()
    tenant_b = await make_tenant()
    owner, _membership, _ownership, owner_role = await make_owner(tenant_id=tenant_a.id)
    role_a = await make_tenant_role(
        tenant_id=tenant_a.id,
        template_name="Кассир",
        level=4,
        name="Кассир A",
    )
    role_b = await make_tenant_role(
        tenant_id=tenant_b.id,
        template_name="Кассир",
        level=4,
        name="Кассир B",
    )
    service = RolesService(RolesRepository(db_session))
    owner_permissions = set(await service.repo.get_role_permissions(owner_role.id))

    visible_ids = {
        role.id
        for role, _codes, _has_hidden_permissions in await service.list_roles_with_permissions(
            actor_id=owner.id,
            tenant_id=tenant_a.id,
            actor_permissions=owner_permissions,
            actor_is_developer=False,
            actor_is_administrator=False,
        )
    }

    assert role_a.id in visible_ids
    assert role_b.id not in visible_ids

    users, total = await service.list_users(tenant_a.id)
    owner_directory = next(user for user, _assignments in users if user.id == owner.id)
    assert total == 1
    assert owner_directory.is_tenant_owner is True
