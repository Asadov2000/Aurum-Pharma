"""roles builder permissions: roles.create + roles.update.

Adds the two permissions that gate the custom-role builder (POST /roles,
PATCH /roles/{id}) and grants them to every system role strong enough to hold
them — owner and up (level <= 3), mirroring how migration 0014 wired
sales.view.*. Sellers (level 4) do not get them.

Existing roles / assignments / seed are untouched; this is purely additive.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (code, group, name, min_level_required, is_dangerous)
_NEW_PERMISSIONS = [
    ("roles.create", "roles", "Создание роли", 3, True),
    ("roles.update", "roles", "Изменение роли", 3, True),
]


def upgrade() -> None:
    for code, group, name, mlr, dangerous in _NEW_PERMISSIONS:
        op.execute(
            "INSERT INTO permission (code, group_code, name, min_level_required, is_dangerous) "
            f"VALUES ('{code}', '{group}', '{name}', {mlr}, {str(dangerous).lower()}) "
            "ON CONFLICT (code) DO NOTHING"
        )
        # Link to every system role whose level qualifies (role.level <= mlr),
        # matching _permissions_for() in migration 0004. code/mlr are controlled
        # constants from _NEW_PERMISSIONS, not user input.
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
