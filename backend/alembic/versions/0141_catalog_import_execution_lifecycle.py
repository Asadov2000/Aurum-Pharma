"""Bound catalog preview/import executions and share tenant admission.

Revision ID: 0141
Revises: 0140
"""

from alembic import op

revision = "0141"
down_revision = "0140"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.catalog_import_job "
        "ADD COLUMN execution_id UUID, "
        "ADD COLUMN execution_started_at TIMESTAMPTZ, "
        "ADD COLUMN deadline_at TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE public.catalog_import_job "
        "DROP CONSTRAINT catalog_import_job_status_check"
    )
    op.execute(
        "ALTER TABLE public.catalog_import_job ADD CONSTRAINT catalog_import_job_status_check "
        "CHECK (status IN ('pending','previewing','validating','importing',"
        "'success','failed','rolled_back'))"
    )
    # Preserve uploaded files, existing results and active work. Legacy messages
    # have no execution token and cannot claim these jobs after worker rollout;
    # lazy recovery releases their slot once this grace period has expired.
    op.execute(
        "UPDATE public.catalog_import_job "
        "SET execution_id = gen_random_uuid(), deadline_at = now() + INTERVAL '10 minutes' "
        "WHERE status = 'importing'"
    )
    op.execute("DROP INDEX public.uq_catalog_import_job_tenant_active")
    op.execute(
        "CREATE UNIQUE INDEX uq_catalog_import_job_tenant_active "
        "ON public.catalog_import_job (tenant_id) WHERE status IN ('previewing','importing')"
    )


def downgrade() -> None:
    # Downgrades are tested only on disposable databases. Preserve every job and
    # its uploaded file; the old application can preview pending jobs again.
    op.execute("UPDATE public.catalog_import_job SET status = 'pending' WHERE status = 'previewing'")
    op.execute("DROP INDEX public.uq_catalog_import_job_tenant_active")
    op.execute(
        "CREATE UNIQUE INDEX uq_catalog_import_job_tenant_active "
        "ON public.catalog_import_job (tenant_id) WHERE status = 'importing'"
    )
    op.execute(
        "ALTER TABLE public.catalog_import_job "
        "DROP CONSTRAINT catalog_import_job_status_check"
    )
    op.execute(
        "ALTER TABLE public.catalog_import_job ADD CONSTRAINT catalog_import_job_status_check "
        "CHECK (status IN ('pending','validating','importing','success','failed','rolled_back'))"
    )
    op.execute(
        "ALTER TABLE public.catalog_import_job DROP COLUMN execution_id, "
        "DROP COLUMN execution_started_at, DROP COLUMN deadline_at"
    )
