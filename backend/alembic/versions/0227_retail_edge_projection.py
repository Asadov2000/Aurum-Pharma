"""Receive independently anchored retail events without enabling delivery.

Revision ID: 0227
Revises: 0226
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

revision = "0227"
down_revision = "0226"
branch_labels = None
depends_on = None


def _sql(sql: str) -> None:
    path = Path(__file__).with_name("0171_unit_cash_intents.py")
    spec = spec_from_file_location("aurum_0227_sql", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load historical SQL executor")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module._sql(sql)


def upgrade() -> None:
    _sql("""
      ALTER TABLE public.sync_sale_projection
       ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1,
       ADD COLUMN stock_command_id UUID,
       ADD CONSTRAINT ck_sync_sale_projection_version CHECK(
        (schema_version=1 AND stock_command_id IS NULL)
        OR (schema_version IN (3,4) AND stock_command_id IS NOT NULL
            AND sale_type='sale' AND NOT is_test));
      GRANT INSERT(schema_version,stock_command_id)
       ON public.sync_sale_projection TO aurum_app;

      CREATE TABLE public.sync_retail_event_anchor (
       event_id UUID PRIMARY KEY,
       tenant_id UUID NOT NULL,branch_id UUID NOT NULL,target_edge_node_id UUID NOT NULL,
       origin_node_id UUID NOT NULL,writer_epoch BIGINT NOT NULL CHECK(writer_epoch>0),
       sequence BIGINT NOT NULL CHECK(sequence>0),
       previous_source_checksum TEXT NOT NULL CHECK(previous_source_checksum ~ '^[0-9a-f]{64}$'),
       previous_projection_checksum TEXT NOT NULL
        CHECK(previous_projection_checksum ~ '^[0-9a-f]{64}$'),
       event_fingerprint TEXT NOT NULL CHECK(event_fingerprint ~ '^[0-9a-f]{64}$'),
       created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),created_by UUID,
       CONSTRAINT uq_sync_retail_anchor_sequence UNIQUE(
        tenant_id,branch_id,target_edge_node_id,origin_node_id,writer_epoch,sequence));
      ALTER TABLE public.sync_retail_event_anchor OWNER TO aurum_schema_owner;
      ALTER TABLE public.sync_retail_event_anchor ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.sync_retail_event_anchor FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON public.sync_retail_event_anchor FROM PUBLIC,aurum_app,aurum_support;
      GRANT SELECT ON public.sync_retail_event_anchor TO aurum_app,aurum_support;
      CREATE POLICY schema_owner ON public.sync_retail_event_anchor TO aurum_schema_owner
       USING(true) WITH CHECK(true);
      CREATE POLICY scoped_reader ON public.sync_retail_event_anchor FOR SELECT
       TO aurum_app,aurum_support USING(
        tenant_id=public.current_tenant_id()
        AND branch_id=NULLIF(current_setting('app.branch_id',true),'')::UUID
        AND target_edge_node_id=NULLIF(current_setting('app.edge_node_id',true),'')::UUID
        AND (session_user='aurum_support' OR
         (session_user='aurum_app' AND NOT public.is_tenant_support_session())));

      CREATE FUNCTION public.trg_retail_edge_anchor_guard() RETURNS TRIGGER
      LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
      BEGIN
       IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'retail_edge_anchor_immutable' USING ERRCODE='55000';
       END IF;
       IF CURRENT_USER<>'aurum_schema_owner' THEN
        RAISE EXCEPTION 'retail_edge_anchor_owner_required' USING ERRCODE='42501';
       END IF;
       RETURN NEW;
      END $$;
      ALTER FUNCTION public.trg_retail_edge_anchor_guard() OWNER TO aurum_schema_owner;
      REVOKE ALL ON FUNCTION public.trg_retail_edge_anchor_guard()
       FROM PUBLIC,aurum_app,aurum_support;
      CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE
       ON public.sync_retail_event_anchor FOR EACH ROW
       EXECUTE FUNCTION public.trg_enforce_tenant_access('pos');
      CREATE TRIGGER a10_retail_edge_anchor_guard BEFORE INSERT OR UPDATE OR DELETE
       ON public.sync_retail_event_anchor FOR EACH ROW
       EXECUTE FUNCTION public.trg_retail_edge_anchor_guard();
      CREATE TRIGGER a10_retail_edge_anchor_no_truncate BEFORE TRUNCATE
       ON public.sync_retail_event_anchor FOR EACH STATEMENT
       EXECUTE FUNCTION public.trg_retail_edge_anchor_guard();
      CREATE TRIGGER z90_retail_edge_anchor_audit AFTER INSERT OR UPDATE OR DELETE
       ON public.sync_retail_event_anchor FOR EACH ROW
       EXECUTE FUNCTION public.trg_audit_log();
    """)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.sync_retail_event_anchor,public.sync_sale_projection
       IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
       IF EXISTS(SELECT 1 FROM public.sync_retail_event_anchor)
          OR EXISTS(SELECT 1 FROM public.sync_sale_projection WHERE schema_version<>1) THEN
        RAISE EXCEPTION 'retail_edge_downgrade_requires_empty_history' USING ERRCODE='55000';
       END IF;
      END $$;
      DROP TABLE public.sync_retail_event_anchor;
      DROP FUNCTION public.trg_retail_edge_anchor_guard();
      ALTER TABLE public.sync_sale_projection
       DROP CONSTRAINT ck_sync_sale_projection_version,
       DROP COLUMN stock_command_id,DROP COLUMN schema_version;
    """)
