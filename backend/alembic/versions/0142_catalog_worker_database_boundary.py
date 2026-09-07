"""Restrict catalog workers to tenant-scoped import data.

Revision ID: 0142
Revises: 0141
"""

from alembic import op

revision = "0142"
down_revision = "0141"
branch_labels = None
depends_on = None

CATALOG_COLUMNS = (
    "brand_name",
    "inn",
    "manufacturer",
    "form",
    "dosage",
    "pack_size",
    "atx_code",
    "dispensing_type",
    "storage_type",
    "category",
    "base_price",
)
JOB_UPDATE_COLUMNS = (
    "status",
    "execution_started_at",
    "total_rows",
    "valid_rows",
    "error_rows",
    "preview_data",
    "errors",
    "finished_at",
    "expires_at_for_rollback",
)
CATALOG_INSERT_COLUMNS = ("tenant_id", "import_job_id", "created_by", *CATALOG_COLUMNS)
BARCODE_INSERT_COLUMNS = ("tenant_id", "catalog_id", "code", "code_type")

CURRENT_TENANT_ID_SQL = """
CREATE OR REPLACE FUNCTION public.current_tenant_id()
RETURNS UUID AS $$
DECLARE
  v_tenant_id TEXT;
  v_support_access_session_id TEXT;
BEGIN
  v_tenant_id := pg_catalog.current_setting('app.tenant_id', true);
  IF v_tenant_id IS NULL OR v_tenant_id = '' THEN
    RETURN NULL;
  END IF;

  v_support_access_session_id := pg_catalog.current_setting(
    'app.support_access_session_id', true
  );
  IF v_support_access_session_id IS NOT NULL AND v_support_access_session_id <> '' THEN
    IF NOT public.is_tenant_support_session() THEN
      RETURN NULL;
    END IF;
  END IF;

  RETURN v_tenant_id::UUID;
EXCEPTION WHEN OTHERS THEN
  RETURN NULL;
END;
$$ LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
"""

# Restore the preceding function body, not the legacy pre-support behavior.
PREVIOUS_CURRENT_TENANT_ID_SQL = CURRENT_TENANT_ID_SQL.replace(
    "  IF v_support_access_session_id IS NOT NULL AND v_support_access_session_id <> '' THEN\n"
    "    IF NOT public.is_tenant_support_session() THEN\n"
    "      RETURN NULL;\n"
    "    END IF;\n"
    "  END IF;",
    "  IF v_support_access_session_id IS NOT NULL\n"
    "    AND v_support_access_session_id <> ''\n"
    "    AND NOT public.is_tenant_support_session()\n"
    "  THEN\n"
    "    RETURN NULL;\n"
    "  END IF;",
)

PREFLIGHT_SQL = """
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles
    WHERE rolname = 'aurum_catalog_worker' AND rolcanlogin
      AND NOT rolinherit AND NOT rolsuper AND NOT rolcreatedb
      AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls
      AND rolconnlimit = 2
  ) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_auth_members AS membership
    JOIN pg_catalog.pg_roles AS actor
      ON actor.oid IN (membership.member, membership.roleid)
    WHERE actor.rolname = 'aurum_catalog_worker'
  ) THEN
    RAISE EXCEPTION 'Bootstrap the restricted aurum_catalog_worker role before migration';
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_catalog.pg_class AS relation
    JOIN pg_catalog.pg_namespace AS schema ON schema.oid = relation.relnamespace
    WHERE schema.nspname = 'public'
      AND relation.relname IN ('catalog_import_job', 'tenant_catalog', 'barcode')
      AND NOT relation.relrowsecurity
  ) THEN
    RAISE EXCEPTION 'Catalog worker requires tenant row-level security';
  END IF;
END
$$;
"""


def _column_grant(privilege: str, table: str, columns: tuple[str, ...]) -> None:
    op.execute(
        f"GRANT {privilege} ({', '.join(columns)}) ON TABLE public.{table} "
        "TO aurum_catalog_worker"
    )


def upgrade() -> None:
    op.execute(PREFLIGHT_SQL)
    # PostgreSQL resolves function privileges when planning an entire SQL
    # expression. A boolean AND cannot guard a call to the support-only helper;
    # separate PL/pgSQL statements keep ordinary workers outside that boundary.
    op.execute(CURRENT_TENANT_ID_SQL)
    op.execute("GRANT USAGE ON SCHEMA public TO aurum_catalog_worker")
    op.execute(
        "GRANT SELECT ON TABLE public.catalog_import_job, public.tenant_catalog, "
        "public.barcode TO aurum_catalog_worker"
    )
    _column_grant("UPDATE", "catalog_import_job", JOB_UPDATE_COLUMNS)
    _column_grant("INSERT", "tenant_catalog", CATALOG_INSERT_COLUMNS)
    _column_grant("UPDATE", "tenant_catalog", CATALOG_COLUMNS)
    _column_grant("INSERT", "barcode", BARCODE_INSERT_COLUMNS)
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.current_tenant_id(), public.current_app_user_id() "
        "TO aurum_catalog_worker"
    )


def downgrade() -> None:
    # Role lifecycle and credentials belong to bootstrap; keep the role and all
    # data, but remove every privilege introduced by this revision.
    for table, privilege, columns in (
        ("catalog_import_job", "UPDATE", JOB_UPDATE_COLUMNS),
        ("tenant_catalog", "INSERT", CATALOG_INSERT_COLUMNS),
        ("tenant_catalog", "UPDATE", CATALOG_COLUMNS),
        ("barcode", "INSERT", BARCODE_INSERT_COLUMNS),
    ):
        op.execute(
            f"REVOKE {privilege} ({', '.join(columns)}) ON TABLE public.{table} "
            "FROM aurum_catalog_worker"
        )
    op.execute(
        "REVOKE SELECT ON TABLE public.catalog_import_job, public.tenant_catalog, "
        "public.barcode FROM aurum_catalog_worker"
    )
    op.execute(
        "REVOKE EXECUTE ON FUNCTION public.current_tenant_id(), public.current_app_user_id() "
        "FROM aurum_catalog_worker"
    )
    op.execute("REVOKE USAGE ON SCHEMA public FROM aurum_catalog_worker")
    op.execute(PREVIOUS_CURRENT_TENANT_ID_SQL)
