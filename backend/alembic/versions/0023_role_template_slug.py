"""role_template.slug — a stable lookup key independent of the display name.

owner provisioning looked templates up by name («Владелец»); a rename would
break it. Add a slug, backfill the two seeded presets ('owner' / 'cashier'),
make it unique + NOT NULL. Additive only — names/ids are untouched, so no data
loss. downgrade drops the column.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0023"
down_revision: str | Sequence[str] | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE role_template ADD COLUMN slug TEXT")
    op.execute("UPDATE role_template SET slug = 'owner' WHERE name = 'Владелец'")
    op.execute("UPDATE role_template SET slug = 'cashier' WHERE name = 'Кассир'")
    # Safety net so SET NOT NULL holds even if other templates exist.
    op.execute("UPDATE role_template SET slug = 'tpl_' || left(id::text, 8) WHERE slug IS NULL")
    op.execute("CREATE UNIQUE INDEX uq_role_template_slug ON role_template (slug)")
    op.execute("ALTER TABLE role_template ALTER COLUMN slug SET NOT NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_role_template_slug")
    op.execute("ALTER TABLE role_template DROP COLUMN slug")
