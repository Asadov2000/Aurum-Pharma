"""roles: permission, role, role_permission, user_assignment + seed

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-19

Tables:
- permission       (global, no RLS — same catalogue for every tenant)
- role             (tenant_id NULLABLE; NULL = system role; RLS lets a
                    tenant see system roles + its own)
- role_permission  (many-to-many; visibility follows role)
- user_assignment  (user × tenant × branch × role; RLS by tenant_id)

Seed in `upgrade()` after the DDL: all 41 permissions and the 4 system
roles (developer, administrator, owner, seller) with their permission
sets. `min_level_required` on each permission is the lowest role level
that's allowed to hold it — system roles get every permission with
`min_level_required >= role.level`, plus seller's per-line additions.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union
from uuid import uuid4

from alembic import op

revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# -----------------------------------------------------------------------------
# Permission catalogue (kept in Python so we can re-use it in seed + tests).
# Order: (code, group, name, min_level_required, is_dangerous).
# -----------------------------------------------------------------------------
PERMISSIONS: list[tuple[str, str, str, int, bool]] = [
    # users
    ("users.view", "users", "Просмотр сотрудников", 3, False),
    ("users.invite", "users", "Приглашение сотрудника", 3, False),
    ("users.update", "users", "Изменение профилей", 3, False),
    ("users.block", "users", "Блокировка сотрудника", 3, True),
    ("users.delete", "users", "Удаление сотрудника", 3, True),
    # roles
    ("roles.assign", "roles", "Назначение ролей", 3, True),
    # branches
    ("branches.view", "branches", "Просмотр точек", 3, False),
    ("branches.create", "branches", "Создание точки", 3, False),
    ("branches.update", "branches", "Изменение точки", 3, False),
    ("branches.delete", "branches", "Удаление точки", 3, True),
    # registers
    ("registers.view", "registers", "Просмотр касс", 3, False),
    ("registers.create", "registers", "Создание кассы", 3, False),
    ("registers.update", "registers", "Изменение кассы", 3, False),
    ("registers.delete", "registers", "Удаление кассы", 3, True),
    # catalog
    ("catalog.view", "catalog", "Просмотр каталога", 4, False),
    ("catalog.create", "catalog", "Добавление позиции", 3, False),
    ("catalog.update", "catalog", "Изменение позиции", 3, False),
    ("catalog.delete", "catalog", "Удаление позиции", 3, True),
    # batches
    ("batches.view", "batches", "Просмотр партий", 4, False),
    ("batches.create", "batches", "Создание партии", 3, False),
    ("batches.update", "batches", "Изменение партии", 3, False),
    ("batches.write_off", "batches", "Списание партии", 3, True),
    # suppliers
    ("suppliers.view", "suppliers", "Просмотр поставщиков", 3, False),
    ("suppliers.create", "suppliers", "Создание поставщика", 3, False),
    ("suppliers.update", "suppliers", "Изменение поставщика", 3, False),
    # incoming
    ("incoming.view", "incoming", "Просмотр приходов", 3, False),
    ("incoming.create", "incoming", "Оформление прихода", 3, False),
    ("incoming.return", "incoming", "Возврат поставщику", 3, True),
    # pos
    ("pos.shift_open", "pos", "Открытие смены", 4, False),
    ("pos.shift_close", "pos", "Закрытие смены", 4, False),
    ("pos.sell", "pos", "Продажа", 4, False),
    ("pos.refund", "pos", "Возврат", 4, True),
    ("pos.handle_prescription", "pos", "Отпуск рецептурного товара", 4, False),
    # reports
    ("reports.view", "reports", "Просмотр отчётов", 3, False),
    ("reports.export", "reports", "Экспорт отчётов", 3, False),
    # audit
    ("audit.view.own", "audit", "Свой аудит", 4, False),
    ("audit.view.tenant", "audit", "Аудит тенанта", 3, False),
    ("audit.view.global", "audit", "Кросс-тенантный аудит", 1, True),
    # settings
    ("settings.update", "settings", "Изменение настроек тенанта", 3, True),
    # tenant
    ("tenant.view", "tenant", "Просмотр данных тенанта", 3, False),
    ("tenant.export.full", "tenant", "Полный экспорт тенанта", 2, True),
]


# -----------------------------------------------------------------------------
# System role definitions (level + extra explicit grants).
# Each system role gets every permission with `min_level_required >= level`,
# plus any extras listed below. Excludes lets us strip the developer-only
# audit.view.global out of the administrator/owner sets.
# -----------------------------------------------------------------------------
SYSTEM_ROLES: list[dict[str, object]] = [
    {
        "name": "developer",
        "description": "Внутренний разработчик платформы",
        "level": 1,
        "excludes": set(),
        "extras": set(),
    },
    {
        "name": "administrator",
        "description": "Сотрудник поддержки",
        "level": 2,
        "excludes": {"audit.view.global"},
        "extras": set(),
    },
    {
        "name": "owner",
        "description": "Владелец аптеки",
        "level": 3,
        "excludes": {"audit.view.global"},
        "extras": set(),
    },
    {
        "name": "seller",
        "description": "Кассир / провизор",
        "level": 4,
        "excludes": set(),
        "extras": set(),  # min_level=4 permissions cover the seller spec set
    },
]


def _permissions_for(level: int, excludes: set[str], extras: set[str]) -> list[str]:
    """Permissions a role of `level` should hold."""
    codes = {code for (code, _, _, mlr, _) in PERMISSIONS if mlr >= level}
    codes -= excludes
    codes |= extras
    return sorted(codes)


def upgrade() -> None:
    # ---- permission ---------------------------------------------------------
    op.execute(
        """
        CREATE TABLE permission (
          code                TEXT PRIMARY KEY,
          group_code          TEXT NOT NULL,
          name                TEXT NOT NULL,
          description         TEXT,
          min_level_required  INT NOT NULL DEFAULT 4
                                CHECK (min_level_required BETWEEN 1 AND 4),
          is_dangerous        BOOLEAN NOT NULL DEFAULT false,
          is_active           BOOLEAN NOT NULL DEFAULT true,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_permission_group ON permission (group_code)")

    # ---- role ---------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE role (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id           UUID REFERENCES tenant(id) ON DELETE CASCADE,
          name                TEXT NOT NULL,
          description         TEXT,
          level               INT NOT NULL CHECK (level BETWEEN 1 AND 4),
          is_system           BOOLEAN NOT NULL DEFAULT false,
          is_active           BOOLEAN NOT NULL DEFAULT true,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by          UUID REFERENCES app_user(id),
          updated_by          UUID REFERENCES app_user(id),
          UNIQUE NULLS NOT DISTINCT (tenant_id, name)
        )
        """
    )
    op.execute("CREATE INDEX ix_role_tenant_level ON role (tenant_id, level)")
    op.execute(
        """
        CREATE TRIGGER trg_role_updated BEFORE UPDATE ON role
          FOR EACH ROW EXECUTE FUNCTION trg_set_updated_meta()
        """
    )
    op.execute("ALTER TABLE role ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON role
          USING (tenant_id IS NULL OR tenant_id = current_tenant_id() OR is_support_session())
        """
    )

    # ---- role_permission ----------------------------------------------------
    op.execute(
        """
        CREATE TABLE role_permission (
          role_id             UUID NOT NULL REFERENCES role(id) ON DELETE CASCADE,
          permission_code     TEXT NOT NULL REFERENCES permission(code),
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (role_id, permission_code)
        )
        """
    )

    # ---- user_assignment ----------------------------------------------------
    op.execute(
        """
        CREATE TABLE user_assignment (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id             UUID NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
          tenant_id           UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
          branch_id           UUID REFERENCES branch(id),
          role_id             UUID NOT NULL REFERENCES role(id),
          password_required   BOOLEAN NOT NULL DEFAULT false,
          is_active           BOOLEAN NOT NULL DEFAULT true,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by          UUID REFERENCES app_user(id),
          updated_by          UUID REFERENCES app_user(id),
          UNIQUE NULLS NOT DISTINCT (user_id, tenant_id, branch_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_user_assignment_user ON user_assignment (user_id) "
        "WHERE is_active = true"
    )
    op.execute(
        "CREATE INDEX ix_user_assignment_tenant ON user_assignment (tenant_id) "
        "WHERE is_active = true"
    )
    op.execute("CREATE INDEX ix_user_assignment_role ON user_assignment (role_id)")
    op.execute(
        """
        CREATE TRIGGER trg_user_assignment_updated BEFORE UPDATE ON user_assignment
          FOR EACH ROW EXECUTE FUNCTION trg_set_updated_meta()
        """
    )
    op.execute("ALTER TABLE user_assignment ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON user_assignment
          USING (tenant_id = current_tenant_id() OR is_support_session())
        """
    )

    # ---- seed permissions ---------------------------------------------------
    conn = op.get_bind()
    from sqlalchemy import text as sa_text

    for code, group, name, mlr, dangerous in PERMISSIONS:
        conn.execute(
            sa_text(
                "INSERT INTO permission (code, group_code, name, "
                "min_level_required, is_dangerous) "
                "VALUES (:c, :g, :n, :m, :d)"
            ),
            {"c": code, "g": group, "n": name, "m": mlr, "d": dangerous},
        )

    # ---- seed system roles + role_permission links -------------------------
    for role_def in SYSTEM_ROLES:
        role_id = uuid4()
        conn.execute(
            sa_text(
                "INSERT INTO role (id, tenant_id, name, description, level, is_system) "
                "VALUES (:id, NULL, :name, :desc, :level, true)"
            ),
            {
                "id": str(role_id),
                "name": role_def["name"],
                "desc": role_def["description"],
                "level": role_def["level"],
            },
        )
        codes = _permissions_for(
            int(role_def["level"]),  # type: ignore[arg-type]
            role_def["excludes"],  # type: ignore[arg-type]
            role_def["extras"],  # type: ignore[arg-type]
        )
        for code in codes:
            conn.execute(
                sa_text(
                    "INSERT INTO role_permission (role_id, permission_code) "
                    "VALUES (:r, :p)"
                ),
                {"r": str(role_id), "p": code},
            )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_assignment CASCADE")
    op.execute("DROP TABLE IF EXISTS role_permission CASCADE")
    op.execute("DROP TABLE IF EXISTS role CASCADE")
    op.execute("DROP TABLE IF EXISTS permission CASCADE")
