"""Move owner / seller out of the system roles into per-tenant roles.

After this revision only `developer` and `administrator` remain system roles.
owner / seller stop being system roles — real roles are tenant-scoped, built
from the «Владелец» / «Кассир» templates seeded in 0019.

For the one tenant that holds live assignments to the system owner/seller roles
(Demo Pharmacy), we:
  1. create tenant roles «Владелец» (level 3) and «Кассир» (level 4) from the
     matching templates,
  2. re-seat its assignments onto those tenant roles,
  3. only then DELETE the system owner/seller roles.
Order matters: user_assignment.role_id is a plain FK (NO ACTION), so the system
roles must be unreferenced before they can be dropped; role_permission cascades.

On a database without Demo Pharmacy (e.g. a fresh CI schema) steps 1–2 simply
affect zero rows and step 3 just removes the seeded system owner/seller.

Onboarding is unchanged: it never auto-assigned an owner role (create_tenant
only builds settings + the wizard/checklist), so there is nothing to switch —
a new tenant's owner role is provisioned manually, exactly as before.

downgrade() restores the prior state: it recreates the system owner/seller roles
from the same templates, re-seats Demo's assignments back, and drops the tenant
roles.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEMO = "Demo Pharmacy"

# (tenant role name, source template name, level, system-role name being replaced)
_MOVES = [
    ("Владелец", "Владелец", 3, "owner"),
    ("Кассир", "Кассир", 4, "seller"),
]


def upgrade() -> None:
    for role_name, template_name, level, _system_name in _MOVES:
        # 1. Tenant role from the template (Demo only; 0 rows elsewhere).
        op.execute(
            f"""
            INSERT INTO role (tenant_id, name, description, level, is_system)
            SELECT t.id, '{role_name}', tpl.description, {level}, false
            FROM tenant t, role_template tpl
            WHERE t.name = '{_DEMO}' AND tpl.name = '{template_name}'
            """
        )
        op.execute(
            f"""
            INSERT INTO role_permission (role_id, permission_code)
            SELECT r.id, rtp.permission_code
            FROM role r
            JOIN tenant t ON t.id = r.tenant_id AND t.name = '{_DEMO}'
            JOIN role_template tpl ON tpl.name = '{template_name}'
            JOIN role_template_permission rtp ON rtp.template_id = tpl.id
            WHERE r.name = '{role_name}' AND r.is_system = false
            """
        )

    for role_name, _template_name, _level, system_name in _MOVES:
        # 2. Re-seat Demo's assignments from the system role to the tenant role.
        op.execute(
            f"""
            UPDATE user_assignment ua
            SET role_id = newr.id
            FROM role oldr, role newr, tenant t
            WHERE ua.role_id = oldr.id
              AND oldr.is_system = true AND oldr.name = '{system_name}'
              AND t.name = '{_DEMO}' AND ua.tenant_id = t.id
              AND newr.tenant_id = t.id AND newr.name = '{role_name}'
              AND newr.is_system = false
            """
        )

    # 3. Now unreferenced — drop the system owner/seller (role_permission cascades).
    op.execute("DELETE FROM role WHERE is_system = true AND name IN ('owner', 'seller')")


def downgrade() -> None:
    # Recreate the system roles from the templates (same sets they were seeded
    # from), with their original descriptions/levels.
    op.execute(
        "INSERT INTO role (tenant_id, name, description, level, is_system) "
        "VALUES (NULL, 'owner', 'Владелец аптеки', 3, true)"
    )
    op.execute(
        "INSERT INTO role (tenant_id, name, description, level, is_system) "
        "VALUES (NULL, 'seller', 'Кассир / провизор', 4, true)"
    )
    for role_name, template_name, _level, system_name in _MOVES:
        op.execute(
            f"""
            INSERT INTO role_permission (role_id, permission_code)
            SELECT r.id, rtp.permission_code
            FROM role r
            JOIN role_template tpl ON tpl.name = '{template_name}'
            JOIN role_template_permission rtp ON rtp.template_id = tpl.id
            WHERE r.is_system = true AND r.name = '{system_name}'
            """
        )
        # Re-seat Demo's assignments back to the system role.
        op.execute(
            f"""
            UPDATE user_assignment ua
            SET role_id = sysr.id
            FROM role oldr, role sysr, tenant t
            WHERE ua.role_id = oldr.id
              AND oldr.is_system = false AND oldr.name = '{role_name}'
              AND t.name = '{_DEMO}' AND ua.tenant_id = t.id
              AND sysr.is_system = true AND sysr.name = '{system_name}'
            """
        )
        # Drop the tenant role (role_permission cascades).
        op.execute(
            f"""
            DELETE FROM role
            WHERE is_system = false AND name = '{role_name}'
              AND tenant_id = (SELECT id FROM tenant WHERE name = '{_DEMO}')
            """
        )
