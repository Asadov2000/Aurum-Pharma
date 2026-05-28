"""sales.view permissions: own (seller sees own receipts) + tenant (owner+).

Mirrors the audit.view.own / audit.view.tenant split:
- sales.view.own    (min_level 4) → every role, incl. seller. Endpoint scopes
  the result set to the caller's own receipts.
- sales.view.tenant (min_level 3) → developer / administrator / owner.
  Endpoint returns every receipt in the tenant.

GET /api/v1/sales gates on sales.view.own (the floor everyone has) and widens
to all-tenant when sales.view.tenant is present.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (code, group, name, min_level_required, is_dangerous, role_levels_that_get_it)
_NEW_PERMISSIONS = [
    ("sales.view.own", "sales", "Просмотр своих чеков", 4, False),
    ("sales.view.tenant", "sales", "Просмотр всех чеков", 3, False),
]


def upgrade() -> None:
    for code, group, name, mlr, dangerous in _NEW_PERMISSIONS:
        op.execute(
            "INSERT INTO permission (code, group_code, name, min_level_required, is_dangerous) "
            f"VALUES ('{code}', '{group}', '{name}', {mlr}, {str(dangerous).lower()}) "
            "ON CONFLICT (code) DO NOTHING"
        )
        # Link to every system role whose level qualifies (role.level <= mlr),
        # matching _permissions_for() in migration 0004. code/mlr are
        # controlled constants from _NEW_PERMISSIONS, not user input.
        op.execute(
            f"INSERT INTO role_permission (role_id, permission_code) "
            f"SELECT r.id, '{code}' FROM role r "
            f"WHERE r.is_system = true AND r.level <= {mlr} "
            f"ON CONFLICT (role_id, permission_code) DO NOTHING"
        )


def downgrade() -> None:
    for code, *_ in _NEW_PERMISSIONS:
        op.execute(f"DELETE FROM role_permission WHERE permission_code = '{code}'")
        op.execute(f"DELETE FROM permission WHERE code = '{code}'")
