"""Persist scoped physical lot restrictions without altering financial boundaries.

Revision ID: 0180
Revises: 0179
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0180"
down_revision: str | Sequence[str] | None = "0179"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HISTORY_ANCHOR = "   RAISE EXCEPTION 'unit_stock_v2_history_immutable' USING ERRCODE='55000';"
BLOCK_BRANCH = """   IF TG_TABLE_NAME='unit_stock_lot_v2' AND TG_OP='UPDATE' THEN
     IF NEW.is_blocked IS DISTINCT FROM OLD.is_blocked
       AND NEW.blocking_revision=OLD.blocking_revision+1
       AND NEW.updated_by=public.current_app_user_id()
       AND (to_jsonb(NEW)-ARRAY['is_blocked','blocking_revision','updated_at','updated_by'])
         IS NOT DISTINCT FROM
           (to_jsonb(OLD)-ARRAY['is_blocked','blocking_revision','updated_at','updated_by']) THEN
       RETURN NEW;
     END IF;
   END IF;
"""
MOVEMENT_ANCHOR = " IF l.id IS NULL OR (l.branch_id,l.catalog_id,l.packaging)"
BLOCKED_MOVEMENT = " IF l.id IS NULL OR l.is_blocked OR (l.branch_id,l.catalog_id,l.packaging)"

ACTOR_SQL = """
CREATE FUNCTION public.trg_unit_lot_v2_block_actor() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF TG_OP='INSERT' THEN
   IF NEW.is_blocked OR NEW.blocking_revision<>0 THEN
     RAISE EXCEPTION 'unit_lot_v2_initial_block_invalid' USING ERRCODE='23514';
   END IF;
   RETURN NEW;
 END IF;
 IF NEW.is_blocked IS NOT DISTINCT FROM OLD.is_blocked THEN
   IF NEW.blocking_revision IS DISTINCT FROM OLD.blocking_revision THEN
     RAISE EXCEPTION 'unit_lot_v2_block_revision_immutable' USING ERRCODE='55000';
   END IF;
   RETURN NEW;
 END IF;
 -- This invoker runs before the private immutable guard: current_user is still
 -- the actual SQL actor, not the definer that maintains stock projections.
 IF current_user<>'aurum_app' OR public.is_support_session()
   OR public.current_tenant_id() IS DISTINCT FROM OLD.tenant_id
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
     OLD.tenant_id,'batches.update',OLD.branch_id),false) THEN
   RAISE EXCEPTION 'unit_lot_v2_block_actor_denied' USING ERRCODE='42501';
 END IF;
 IF NEW.blocking_revision IS DISTINCT FROM OLD.blocking_revision
   OR (to_jsonb(NEW)-ARRAY['is_blocked','updated_at','updated_by'])
     IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['is_blocked','updated_at','updated_by']) THEN
   RAISE EXCEPTION 'unit_lot_v2_block_fields_immutable' USING ERRCODE='55000';
 END IF;
 NEW.blocking_revision:=OLD.blocking_revision+1;
 NEW.updated_at:=clock_timestamp();
 NEW.updated_by:=public.current_app_user_id();
 RETURN NEW;
END $$;
"""


def _replace_function(name: str, old: str, new: str) -> None:
    """Change one verified body fragment; preserve the current private ACL/owner."""
    definition = op.get_bind().scalar(
        text("SELECT pg_get_functiondef(CAST(:name AS regprocedure))"), {"name": name + "()"}
    )
    if not isinstance(definition, str) or definition.count(old) != 1:
        raise RuntimeError("Physical lot historical guard differs from the expected definition")
    op.get_bind().exec_driver_sql(definition.replace(old, new, 1))


def _sql(sql: str) -> None:
    """asyncpg prepares one statement; preserve complete quoted function bodies."""
    start = 0
    for match in re.finditer(r"\$\$.*?\$\$|'(?:''|[^'])*'|;", sql, flags=re.DOTALL):
        if match.group() == ";":
            statement = sql[start : match.start()].strip()
            if statement:
                op.get_bind().exec_driver_sql(statement)
            start = match.end()
    if sql[start:].strip():
        op.get_bind().exec_driver_sql(sql[start:])


def upgrade() -> None:
    _sql("""
      ALTER TABLE public.unit_stock_lot_v2
        ADD COLUMN is_blocked BOOLEAN NOT NULL DEFAULT false,
        ADD COLUMN blocking_revision BIGINT NOT NULL DEFAULT 0,
        ADD CONSTRAINT ck_unit_lot_v2_block_revision CHECK(blocking_revision>=0);
    """)
    _replace_function(
        "public.trg_unit_stock_v2_guard", HISTORY_ANCHOR, BLOCK_BRANCH + HISTORY_ANCHOR
    )
    _replace_function("public.trg_unit_movement_v2_guard", MOVEMENT_ANCHOR, BLOCKED_MOVEMENT)
    _sql(ACTOR_SQL)
    _sql("""
      ALTER FUNCTION public.trg_unit_lot_v2_block_actor() OWNER TO aurum_schema_owner;
      REVOKE ALL ON FUNCTION public.trg_unit_lot_v2_block_actor()
        FROM PUBLIC,aurum_app,aurum_support;
      GRANT UPDATE(is_blocked) ON public.unit_stock_lot_v2 TO aurum_app;
      CREATE POLICY tenant_blocking ON public.unit_stock_lot_v2 FOR UPDATE TO aurum_app
        USING(tenant_id=public.current_tenant_id() AND
          public.tenant_actor_has_scoped_permission(tenant_id,'batches.update',branch_id))
        WITH CHECK(tenant_id=public.current_tenant_id() AND
          public.tenant_actor_has_scoped_permission(tenant_id,'batches.update',branch_id));
      CREATE TRIGGER a08_unit_lot_v2_block_actor BEFORE INSERT OR UPDATE
        ON public.unit_stock_lot_v2 FOR EACH ROW
        EXECUTE FUNCTION public.trg_unit_lot_v2_block_actor();
    """)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.unit_stock_lot_v2,public.unit_stock_movement_v2 IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.unit_stock_lot_v2 WHERE is_blocked OR blocking_revision>0)
        THEN RAISE EXCEPTION 'unit_lot_v2_block_downgrade_loses_history' USING ERRCODE='55000';
        END IF;
      END $$;
    """)
    _replace_function(
        "public.trg_unit_stock_v2_guard", BLOCK_BRANCH + HISTORY_ANCHOR, HISTORY_ANCHOR
    )
    _replace_function("public.trg_unit_movement_v2_guard", BLOCKED_MOVEMENT, MOVEMENT_ANCHOR)
    _sql("""
      DROP TRIGGER a08_unit_lot_v2_block_actor ON public.unit_stock_lot_v2;
      DROP FUNCTION public.trg_unit_lot_v2_block_actor();
      DROP POLICY tenant_blocking ON public.unit_stock_lot_v2;
      REVOKE UPDATE(is_blocked) ON public.unit_stock_lot_v2 FROM aurum_app;
      ALTER TABLE public.unit_stock_lot_v2 DROP CONSTRAINT ck_unit_lot_v2_block_revision,
        DROP COLUMN blocking_revision,DROP COLUMN is_blocked;
    """)
