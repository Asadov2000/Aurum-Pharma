"""Rename 'тенант' → 'аптека' in three permission names.

"Тенант" is internal jargon; the role builder is seen by pharmacy owners, so the
labels for settings.update / tenant.view / tenant.export.full read «аптека»
instead. Only the `name` text changes — codes, groups and behaviour are
untouched. downgrade() restores the original names.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text as sa_text

revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (code, new name, old name)
_RENAMES = [
    ("settings.update", "Изменение настроек аптеки", "Изменение настроек тенанта"),
    ("tenant.view", "Просмотр данных аптеки", "Просмотр данных тенанта"),
    ("tenant.export.full", "Полный экспорт данных аптеки", "Полный экспорт тенанта"),
]


def upgrade() -> None:
    conn = op.get_bind()
    for code, new_name, _old in _RENAMES:
        conn.execute(
            sa_text("UPDATE permission SET name = :n WHERE code = :c"),
            {"n": new_name, "c": code},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for code, _new, old_name in _RENAMES:
        conn.execute(
            sa_text("UPDATE permission SET name = :n WHERE code = :c"),
            {"n": old_name, "c": code},
        )
