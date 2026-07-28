"""list_users — pagination + batched assignments (no per-user N+1)."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.roles.models import UserAssignment
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
    branch_id: UUID | None = None,
    phone: str | None = None,
    activate: bool = False,
) -> tuple[UUID, UserAssignment]:
    account, _membership = await service.create_tenant_account(
        tenant_id=tenant_id,
        email=email,
        full_name=name,
        phone=phone,
    )
    assignment = await service.assign_role(
        actor_id=actor_id,
        actor_permissions=set(),
        actor_permission_scopes={},
        actor_is_developer=True,
        actor_is_administrator=False,
        tenant_id=tenant_id,
        target_user_id=account.id,
        role_id=role_id,
        branch_id=branch_id,
        password_required=False,
    )
    if activate:
        await service.activate_membership(
            actor_id=actor_id,
            tenant_id=tenant_id,
            target_user_id=account.id,
        )
    return account.id, assignment


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


async def test_search_users_filters_active_assignments_and_branch_scope(
    db_session: AsyncSession,
    make_tenant,
    make_tenant_role,
) -> None:
    tenant = await make_tenant()
    other_tenant = await make_tenant()
    foundation = FoundationService(FoundationRepository(db_session))
    branch_a = await foundation.create_branch(tenant_id=tenant.id, fields={"name": "A"})
    branch_b = await foundation.create_branch(tenant_id=tenant.id, fields={"name": "B"})
    other_branch = await foundation.create_branch(
        tenant_id=other_tenant.id,
        fields={"name": "Other"},
    )
    cashier = await make_tenant_role(
        tenant_id=tenant.id,
        template_name="Кассир",
        level=4,
        name="Cashier search",
    )
    manager = await make_tenant_role(
        tenant_id=tenant.id,
        template_name="Кассир",
        level=4,
        name="Manager search",
    )
    other_cashier = await make_tenant_role(
        tenant_id=other_tenant.id,
        template_name="Кассир",
        level=4,
        name="Other cashier search",
    )
    service = RolesService(RolesRepository(db_session))
    actor_id = uuid4()

    alice_id, _ = await _create_attached_user(
        service,
        actor_id=actor_id,
        tenant_id=tenant.id,
        role_id=cashier.id,
        email="alice.search@aurum.tj",
        name="Alpha Alice",
        phone="+992900000001",
        activate=True,
    )
    bob_id, _ = await _create_attached_user(
        service,
        actor_id=actor_id,
        tenant_id=tenant.id,
        role_id=cashier.id,
        branch_id=branch_a.id,
        email="bob.search@aurum.tj",
        name="Beta Bob",
        activate=True,
    )
    await _create_attached_user(
        service,
        actor_id=actor_id,
        tenant_id=tenant.id,
        role_id=cashier.id,
        branch_id=branch_b.id,
        email="carol.search@aurum.tj",
        name="Gamma Carol",
        activate=True,
    )
    await _create_attached_user(
        service,
        actor_id=actor_id,
        tenant_id=tenant.id,
        role_id=manager.id,
        branch_id=branch_a.id,
        email="dana.search@aurum.tj",
        name="Delta Dana",
        activate=True,
    )
    _inactive_id, inactive_assignment = await _create_attached_user(
        service,
        actor_id=actor_id,
        tenant_id=tenant.id,
        role_id=cashier.id,
        branch_id=branch_a.id,
        email="evan.search@aurum.tj",
        name="Epsilon Evan",
        activate=True,
    )
    await service.repo.deactivate_assignment(inactive_assignment.id, tenant_id=tenant.id)
    pending_id, _ = await _create_attached_user(
        service,
        actor_id=actor_id,
        tenant_id=tenant.id,
        role_id=cashier.id,
        branch_id=branch_a.id,
        email="pat.search@aurum.tj",
        name="Pending Pat",
    )
    await _create_attached_user(
        service,
        actor_id=actor_id,
        tenant_id=other_tenant.id,
        role_id=other_cashier.id,
        branch_id=other_branch.id,
        email="alpha.other@aurum.tj",
        name="Alpha Other Tenant",
        activate=True,
    )

    pairs, total = await service.search_users(
        tenant.id,
        role_id=cashier.id,
        branch_id=branch_a.id,
        status="active",
    )
    assert total == 2
    assert {user.id for user, _assignments in pairs} == {alice_id, bob_id}

    by_phone, phone_total = await service.search_users(
        tenant.id,
        q="992900000001",
    )
    assert phone_total == 1
    assert by_phone[0][0].id == alice_id

    pending, pending_total = await service.search_users(
        tenant.id,
        status="pending",
        visible_branch_ids={branch_a.id},
    )
    assert pending_total == 1
    assert pending[0][0].id == pending_id

    scoped, scoped_total = await service.search_users(
        tenant.id,
        visible_branch_ids={branch_a.id},
        page=1,
        page_size=2,
    )
    assert scoped_total == 4
    assert len(scoped) == 2

    outside_scope, outside_total = await service.search_users(
        tenant.id,
        branch_id=branch_b.id,
        visible_branch_ids={branch_a.id},
    )
    assert outside_scope == []
    assert outside_total == 0


async def test_search_users_pagination_is_stable(
    db_session: AsyncSession,
    make_tenant,
    make_tenant_role,
) -> None:
    tenant = await make_tenant()
    role = await make_tenant_role(
        tenant_id=tenant.id,
        template_name="Кассир",
        level=4,
        name="Stable search role",
    )
    service = RolesService(RolesRepository(db_session))
    actor_id = uuid4()
    for index in range(5):
        await _create_attached_user(
            service,
            actor_id=actor_id,
            tenant_id=tenant.id,
            role_id=role.id,
            email=f"stable-{index}@aurum.tj",
            name="Same Name",
            activate=True,
        )

    first, first_total = await service.search_users(tenant.id, page=1, page_size=2)
    second, second_total = await service.search_users(tenant.id, page=2, page_size=2)
    repeated, repeated_total = await service.search_users(tenant.id, page=1, page_size=2)

    assert first_total == second_total == repeated_total == 5
    assert [user.id for user, _ in first] == [user.id for user, _ in repeated]
    assert {user.id for user, _ in first}.isdisjoint({user.id for user, _ in second})
