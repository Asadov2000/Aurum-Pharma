"""role templates: a global recommendation library for the role builder.

Two tables, modelled on role / role_permission but global (no tenant_id, no
RLS — every tenant sees the same presets, like the permission catalogue):
- role_template            (name, description, is_system, is_active)
- role_template_permission (template_id × permission_code)

Seeds two starter templates whose permission sets are copied EXACTLY from the
current system roles: «Владелец» <- owner, «Кассир» <- seller. Copying via
SELECT (rather than a hand-written list) guarantees the presets match whatever
those roles hold at this revision — including the perms added in 0014 / 0018.

Purely additive: existing roles / assignments / seed are untouched. A template
is only a hint — real roles are still created through POST /roles, where
anti-escalation applies, so a template can never grant reach the actor lacks.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (template name, description, source system-role name)
_TEMPLATES = [
    ("Владелец", "Полный доступ к аптеке: управление, закупки, отчёты.", "owner"),
    ("Кассир", "Работа за кассой: продажи, смены, отпуск товара.", "seller"),
]


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE role_template (
          id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          name        TEXT NOT NULL,
          description TEXT,
          is_system   BOOLEAN NOT NULL DEFAULT true,
          is_active   BOOLEAN NOT NULL DEFAULT true,
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_role_template_name UNIQUE (name)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE role_template_permission (
          template_id     UUID NOT NULL REFERENCES role_template(id) ON DELETE CASCADE,
          permission_code TEXT NOT NULL REFERENCES permission(code),
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (template_id, permission_code)
        )
        """
    )

    for name, description, source_role in _TEMPLATES:
        # name/description/source_role are controlled constants, not user input.
        op.execute(
            f"INSERT INTO role_template (name, description) "
            f"VALUES ('{name}', '{description}')"
        )
        op.execute(
            f"INSERT INTO role_template_permission (template_id, permission_code) "
            f"SELECT t.id, rp.permission_code "
            f"FROM role_template t "
            f"JOIN role r ON r.name = '{source_role}' AND r.is_system = true "
            f"JOIN role_permission rp ON rp.role_id = r.id "
            f"WHERE t.name = '{name}'"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS role_template_permission CASCADE")
    op.execute("DROP TABLE IF EXISTS role_template CASCADE")
