from __future__ import annotations

from uuid import UUID, uuid4

from app.core.deps import CurrentUser
from app.domains.pos.router import (
    _can_manage_tenant_sales,
    _can_manage_tenant_shifts,
    _sale_manage_branch_scope,
    _shift_manage_branch_scope,
)


def _user(
    *,
    permissions: set[str],
    scopes: dict[str, frozenset[UUID] | None],
) -> CurrentUser:
    return CurrentUser(
        user_id=uuid4(),
        tenant_id=uuid4(),
        is_developer=False,
        is_administrator=False,
        permissions=permissions,
        permission_scopes=scopes,
    )


def test_sales_view_permission_does_not_grant_sale_management() -> None:
    user = _user(
        permissions={"sales.view.tenant"},
        scopes={"sales.view.tenant": None},
    )

    assert _can_manage_tenant_sales(user) is False
    assert _sale_manage_branch_scope(user) == set()


def test_manage_sales_permission_respects_tenant_and_branch_scope() -> None:
    tenant_manager = _user(
        permissions={"pos.manage_sales"},
        scopes={"pos.manage_sales": None},
    )
    branch_id = uuid4()
    branch_manager = _user(
        permissions={"pos.manage_sales"},
        scopes={"pos.manage_sales": frozenset({branch_id})},
    )

    assert _can_manage_tenant_sales(tenant_manager) is True
    assert _sale_manage_branch_scope(tenant_manager) is None
    assert _can_manage_tenant_sales(branch_manager) is False
    assert _sale_manage_branch_scope(branch_manager) == {branch_id}


def test_sale_management_does_not_grant_shift_management() -> None:
    user = _user(
        permissions={"pos.manage_sales"},
        scopes={"pos.manage_sales": None},
    )

    assert _can_manage_tenant_shifts(user) is False
    assert _shift_manage_branch_scope(user) == set()


def test_manage_shifts_permission_respects_tenant_and_branch_scope() -> None:
    tenant_manager = _user(
        permissions={"pos.manage_shifts"},
        scopes={"pos.manage_shifts": None},
    )
    branch_id = uuid4()
    branch_manager = _user(
        permissions={"pos.manage_shifts"},
        scopes={"pos.manage_shifts": frozenset({branch_id})},
    )

    assert _can_manage_tenant_shifts(tenant_manager) is True
    assert _shift_manage_branch_scope(tenant_manager) is None
    assert _can_manage_tenant_shifts(branch_manager) is False
    assert _shift_manage_branch_scope(branch_manager) == {branch_id}
