"""Index active catalog names used by bounded import duplicate lookup."""

from alembic import op

revision = "0143"
down_revision = "0142"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Copies remain legal: this accelerates lookup without introducing a new
    # uniqueness rule or changing the existing database/Python case comparison.
    op.execute(
        "CREATE INDEX ix_tenant_catalog_import_name "
        "ON public.tenant_catalog (tenant_id, lower(brand_name)) "
        "WHERE deleted_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX public.ix_tenant_catalog_import_name")
