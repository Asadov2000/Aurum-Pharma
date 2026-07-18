"""Authorization snapshots must carry transactionally current revisions."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.domains.foundation.models import Branch
from app.domains.roles.models import Permission, Role, RolePermission, UserAssignment
from app.domains.roles.repository import RolesRepository


async def _assign(
    db_session: AsyncSession,
    *,
    user_id,
    tenant_id,
    role_id,
    branch_id=None,
) -> UserAssignment:
    assignment = UserAssignment(
        user_id=user_id,
        tenant_id=tenant_id,
        role_id=role_id,
        branch_id=branch_id,
    )
    db_session.add(assignment)
    await db_session.flush()
    return assignment


async def test_snapshot_contains_active_permissions_and_revisions(
    db_session: AsyncSession,
    make_tenant,
    make_tenant_role,
    make_user,
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    role = await make_tenant_role(
        tenant_id=tenant.id,
        template_name="Кассир",
        level=4,
    )
    await _assign(
        db_session,
        user_id=user.id,
        tenant_id=tenant.id,
        role_id=role.id,
    )

    snapshot = await RolesRepository(db_session).authorization_snapshot(user.id, tenant.id)

    assert snapshot.policy_revision >= 1
    assert snapshot.subject_revision >= 1
    assert "pos.sell" in snapshot.permissions


async def test_capability_scope_stays_bound_to_the_assignment_that_granted_it(
    db_session: AsyncSession,
    make_tenant,
    make_user,
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    branch_a = Branch(tenant_id=tenant.id, name="Scope A")
    branch_b = Branch(tenant_id=tenant.id, name="Scope B")
    sell_role = Role(tenant_id=tenant.id, name="Sell only A", level=4)
    unrelated_branch_role = Role(
        tenant_id=tenant.id,
        name="Unrelated only B",
        level=4,
    )
    unrelated_tenant_role = Role(
        tenant_id=tenant.id,
        name="Unrelated tenant-wide",
        level=4,
    )
    db_session.add_all(
        [
            branch_a,
            branch_b,
            sell_role,
            unrelated_branch_role,
            unrelated_tenant_role,
        ]
    )
    await db_session.flush()
    db_session.add_all(
        [
            RolePermission(role_id=sell_role.id, permission_code="pos.sell"),
            RolePermission(
                role_id=unrelated_branch_role.id,
                permission_code="branches.view",
            ),
            RolePermission(
                role_id=unrelated_branch_role.id,
                permission_code="settings.update",
            ),
            RolePermission(
                role_id=unrelated_tenant_role.id,
                permission_code="catalog.view",
            ),
        ]
    )
    await db_session.flush()
    await _assign(
        db_session,
        user_id=user.id,
        tenant_id=tenant.id,
        role_id=sell_role.id,
        branch_id=branch_a.id,
    )
    await _assign(
        db_session,
        user_id=user.id,
        tenant_id=tenant.id,
        role_id=unrelated_branch_role.id,
        branch_id=branch_b.id,
    )
    await _assign(
        db_session,
        user_id=user.id,
        tenant_id=tenant.id,
        role_id=unrelated_tenant_role.id,
    )

    snapshot = await RolesRepository(db_session).authorization_snapshot(
        user.id,
        tenant.id,
    )
    current = CurrentUser(
        user_id=user.id,
        tenant_id=tenant.id,
        is_developer=False,
        is_administrator=False,
        permissions=set(snapshot.permissions),
        permission_scopes=dict(snapshot.permission_scopes),
    )

    assert snapshot.permission_scopes["pos.sell"] == frozenset({branch_a.id})
    assert snapshot.permission_scopes["branches.view"] == frozenset({branch_b.id})
    assert snapshot.permission_scopes["catalog.view"] is None
    assert "settings.update" not in snapshot.permissions
    assert current.can_access_branch("pos.sell", branch_a.id)
    assert not current.can_access_branch("pos.sell", branch_b.id)


async def test_assignment_mutation_advances_only_subject_revision(
    db_session: AsyncSession,
    make_tenant,
    make_tenant_role,
    make_user,
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    role = await make_tenant_role(
        tenant_id=tenant.id,
        template_name="Кассир",
        level=4,
    )
    assignment = await _assign(
        db_session,
        user_id=user.id,
        tenant_id=tenant.id,
        role_id=role.id,
    )
    repository = RolesRepository(db_session)
    before = await repository.authorization_snapshot(user.id, tenant.id)

    assignment.is_active = False
    await db_session.flush()
    after = await repository.authorization_snapshot(user.id, tenant.id)

    assert after.policy_revision == before.policy_revision
    assert after.subject_revision > before.subject_revision
    assert after.permissions == frozenset()


async def test_role_mutation_advances_policy_revision(
    db_session: AsyncSession,
    make_tenant,
    make_tenant_role,
    make_user,
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    role = await make_tenant_role(
        tenant_id=tenant.id,
        template_name="Кассир",
        level=4,
    )
    await _assign(
        db_session,
        user_id=user.id,
        tenant_id=tenant.id,
        role_id=role.id,
    )
    repository = RolesRepository(db_session)
    before = await repository.authorization_snapshot(user.id, tenant.id)

    role.is_active = False
    await db_session.flush()
    after = await repository.authorization_snapshot(user.id, tenant.id)

    assert after.policy_revision > before.policy_revision
    assert after.subject_revision == before.subject_revision
    assert after.permissions == frozenset()


async def test_role_permission_mutation_advances_policy_revision(
    db_session: AsyncSession,
    make_tenant,
    make_tenant_role,
    make_user,
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    role = await make_tenant_role(
        tenant_id=tenant.id,
        template_name="Кассир",
        level=4,
    )
    await _assign(
        db_session,
        user_id=user.id,
        tenant_id=tenant.id,
        role_id=role.id,
    )
    repository = RolesRepository(db_session)
    before = await repository.authorization_snapshot(user.id, tenant.id)
    link = (
        await db_session.execute(
            select(RolePermission).where(
                RolePermission.role_id == role.id,
                RolePermission.permission_code == "pos.sell",
            )
        )
    ).scalar_one()

    await db_session.delete(link)
    await db_session.flush()
    after = await repository.authorization_snapshot(user.id, tenant.id)

    assert after.policy_revision > before.policy_revision
    assert after.subject_revision == before.subject_revision
    assert "pos.sell" not in after.permissions


async def test_tenant_policy_revisions_do_not_cross_tenants(
    db_session: AsyncSession,
    make_tenant,
    make_tenant_role,
    make_user,
) -> None:
    first_tenant = await make_tenant()
    second_tenant = await make_tenant()
    first_user = await make_user(home_tenant_id=first_tenant.id)
    second_user = await make_user(home_tenant_id=second_tenant.id)
    first_role = await make_tenant_role(
        tenant_id=first_tenant.id,
        template_name="Кассир",
        level=4,
    )
    second_role = await make_tenant_role(
        tenant_id=second_tenant.id,
        template_name="Кассир",
        level=4,
    )
    await _assign(
        db_session,
        user_id=first_user.id,
        tenant_id=first_tenant.id,
        role_id=first_role.id,
    )
    await _assign(
        db_session,
        user_id=second_user.id,
        tenant_id=second_tenant.id,
        role_id=second_role.id,
    )
    repository = RolesRepository(db_session)
    first_before = await repository.authorization_snapshot(first_user.id, first_tenant.id)
    second_before = await repository.authorization_snapshot(second_user.id, second_tenant.id)

    first_role.is_active = False
    await db_session.flush()
    first_after = await repository.authorization_snapshot(first_user.id, first_tenant.id)
    second_after = await repository.authorization_snapshot(second_user.id, second_tenant.id)

    assert first_after.policy_revision > first_before.policy_revision
    assert second_after.policy_revision == second_before.policy_revision


async def test_global_role_mutation_advances_all_tenant_policy_revisions(
    db_session: AsyncSession,
    make_tenant,
    make_user,
    system_roles,
) -> None:
    first_tenant = await make_tenant()
    second_tenant = await make_tenant()
    first_user = await make_user(home_tenant_id=first_tenant.id)
    second_user = await make_user(home_tenant_id=second_tenant.id)
    global_role = system_roles["administrator"]
    await _assign(
        db_session,
        user_id=first_user.id,
        tenant_id=first_tenant.id,
        role_id=global_role.id,
    )
    await _assign(
        db_session,
        user_id=second_user.id,
        tenant_id=second_tenant.id,
        role_id=global_role.id,
    )
    repository = RolesRepository(db_session)
    first_before = await repository.authorization_snapshot(first_user.id, first_tenant.id)
    second_before = await repository.authorization_snapshot(second_user.id, second_tenant.id)

    global_role.is_active = False
    await db_session.flush()
    first_after = await repository.authorization_snapshot(first_user.id, first_tenant.id)
    second_after = await repository.authorization_snapshot(second_user.id, second_tenant.id)

    assert first_after.policy_revision > first_before.policy_revision
    assert second_after.policy_revision > second_before.policy_revision
    assert first_after.permissions == frozenset()
    assert second_after.permissions == frozenset()


async def test_global_role_permission_mutation_advances_all_policy_revisions(
    db_session: AsyncSession,
    make_tenant,
    make_user,
    system_roles,
) -> None:
    first_tenant = await make_tenant()
    second_tenant = await make_tenant()
    first_user = await make_user(home_tenant_id=first_tenant.id)
    second_user = await make_user(home_tenant_id=second_tenant.id)
    global_role = system_roles["administrator"]
    await _assign(
        db_session,
        user_id=first_user.id,
        tenant_id=first_tenant.id,
        role_id=global_role.id,
    )
    await _assign(
        db_session,
        user_id=second_user.id,
        tenant_id=second_tenant.id,
        role_id=global_role.id,
    )
    repository = RolesRepository(db_session)
    first_before = await repository.authorization_snapshot(first_user.id, first_tenant.id)
    second_before = await repository.authorization_snapshot(second_user.id, second_tenant.id)
    link = (
        await db_session.execute(
            select(RolePermission).where(
                RolePermission.role_id == global_role.id,
                RolePermission.permission_code == "pos.sell",
            )
        )
    ).scalar_one()

    await db_session.delete(link)
    await db_session.flush()
    first_after = await repository.authorization_snapshot(first_user.id, first_tenant.id)
    second_after = await repository.authorization_snapshot(second_user.id, second_tenant.id)

    assert first_after.policy_revision > first_before.policy_revision
    assert second_after.policy_revision > second_before.policy_revision
    assert "pos.sell" not in first_after.permissions
    assert "pos.sell" not in second_after.permissions


async def test_global_permission_mutation_advances_all_tenant_policy_revisions(
    db_session: AsyncSession,
    make_tenant,
    make_tenant_role,
    make_user,
) -> None:
    first_tenant = await make_tenant()
    second_tenant = await make_tenant()
    first_user = await make_user(home_tenant_id=first_tenant.id)
    second_user = await make_user(home_tenant_id=second_tenant.id)
    first_role = await make_tenant_role(
        tenant_id=first_tenant.id,
        template_name="Кассир",
        level=4,
    )
    second_role = await make_tenant_role(
        tenant_id=second_tenant.id,
        template_name="Кассир",
        level=4,
    )
    await _assign(
        db_session,
        user_id=first_user.id,
        tenant_id=first_tenant.id,
        role_id=first_role.id,
    )
    await _assign(
        db_session,
        user_id=second_user.id,
        tenant_id=second_tenant.id,
        role_id=second_role.id,
    )
    repository = RolesRepository(db_session)
    first_before = await repository.authorization_snapshot(first_user.id, first_tenant.id)
    second_before = await repository.authorization_snapshot(second_user.id, second_tenant.id)
    permission = await db_session.get(Permission, "pos.sell")
    assert permission is not None

    permission.is_active = False
    await db_session.flush()
    first_after = await repository.authorization_snapshot(first_user.id, first_tenant.id)
    second_after = await repository.authorization_snapshot(second_user.id, second_tenant.id)

    assert first_after.policy_revision > first_before.policy_revision
    assert second_after.policy_revision > second_before.policy_revision
    assert "pos.sell" not in first_after.permissions
    assert "pos.sell" not in second_after.permissions


async def test_rollback_does_not_leave_revision_advanced(
    db_session: AsyncSession,
    make_tenant,
    make_tenant_role,
    make_user,
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    role = await make_tenant_role(
        tenant_id=tenant.id,
        template_name="Кассир",
        level=4,
    )
    assignment = await _assign(
        db_session,
        user_id=user.id,
        tenant_id=tenant.id,
        role_id=role.id,
    )
    repository = RolesRepository(db_session)
    before = await repository.authorization_snapshot(user.id, tenant.id)

    savepoint = await db_session.begin_nested()
    assignment.is_active = False
    await db_session.flush()
    inside_rollback = await repository.authorization_snapshot(user.id, tenant.id)
    await savepoint.rollback()

    after = await repository.authorization_snapshot(user.id, tenant.id)

    assert inside_rollback.subject_revision > before.subject_revision
    assert after.policy_revision == before.policy_revision
    assert after.subject_revision == before.subject_revision
    assert "pos.sell" in after.permissions
