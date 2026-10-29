"""Explicit physical read snapshot v2; retain immutable legacy v1 history.

Revision ID: 0228
Revises: 0227
"""

from sqlalchemy import text

from alembic import op

revision = "0228"
down_revision = "0227"
branch_labels = None
depends_on = None

TABLES = ("sync_read_snapshot", "sync_read_snapshot_component", "sync_read_snapshot_chunk")
HEADER_SIGNATURE = "public.trg_guard_sync_read_projection_header()"
STORAGE_SIGNATURE = "public.trg_guard_sync_read_snapshot_storage()"
INVENTORY_FILTER = "WHERE c.snapshot_id = NEW.snapshot_id AND c.component = 'inventory'"
VERSIONED_FILTER = (
    INVENTORY_FILTER + " AND (NEW.projection_version=1 OR item->>'record_type'='inventory_batch')"
)

PHYSICAL_GUARD = """
      IF TG_OP <> 'DELETE' THEN
        IF NEW.projection_version IS DISTINCT FROM
          (SELECT schema_version FROM public.sync_read_snapshot WHERE snapshot_id=NEW.snapshot_id)
          OR jsonb_typeof(NEW.payload) IS DISTINCT FROM 'array'
          OR NEW.physical_count IS DISTINCT FROM jsonb_array_length(NEW.payload)
          OR NEW.physical_count>10000 OR octet_length(NEW.payload::text)>8388608
          OR (NEW.projection_version=1 AND NEW.physical_count<>0)
        THEN RAISE EXCEPTION 'Physical read projection version or bounds differ'
          USING ERRCODE='23514'; END IF;
        IF NEW.projection_version=2 AND (
          EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.payload) item
            WHERE jsonb_typeof(item) IS DISTINCT FROM 'object'
              OR item->>'id' IS NULL
              OR item->>'id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
              OR item->>'tenant_id' IS DISTINCT FROM NEW.tenant_id::text
              OR item->>'branch_id' IS DISTINCT FROM NEW.branch_id::text)
          OR EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.payload) item
            GROUP BY item->>'record_type',item->>'id' HAVING count(*)>1)
          OR EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.payload) item
            WHERE item->>'record_type' IN ('physical_lot','physical_legacy_lot')
            GROUP BY item->>'id' HAVING count(*)>1)
          OR EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.payload) config
            WHERE config->>'record_type'='physical_catalog_config' AND NOT EXISTS (
              SELECT 1 FROM public.sync_read_snapshot_chunk c,
                LATERAL jsonb_array_elements(c.payload->'items') catalog
              WHERE c.snapshot_id=NEW.snapshot_id AND c.component='catalog'
                AND catalog->>'record_type'='catalog_product'
                AND catalog->>'id'=config->>'id'))
          OR (NEW.projection_version=2 AND EXISTS (
            SELECT 1 FROM public.sync_read_snapshot_chunk c,
              LATERAL jsonb_array_elements(c.payload->'items') catalog
            WHERE c.snapshot_id=NEW.snapshot_id AND c.component='catalog'
              AND catalog->>'record_type'='catalog_product' AND NOT EXISTS (
                SELECT 1 FROM jsonb_array_elements(NEW.payload) config
                WHERE config->>'record_type'='physical_catalog_config'
                  AND config->>'id'=catalog->>'id')))
          OR EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.payload) lot
            WHERE lot->>'record_type' IN ('physical_lot','physical_legacy_lot') AND NOT EXISTS (
              SELECT 1 FROM jsonb_array_elements(NEW.payload) config
              WHERE config->>'record_type'='physical_catalog_config'
                AND config->>'id'=lot->>'catalog_id'))
          OR EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.payload) lot,
            LATERAL jsonb_array_elements(CASE
              WHEN jsonb_typeof(lot->'opened_packs')='array' THEN lot->'opened_packs'
              ELSE '[]'::jsonb END) pack
            WHERE lot->>'record_type' IN ('physical_lot','physical_legacy_lot')
            GROUP BY pack->>'id' HAVING count(*)>1)
          OR EXISTS (SELECT 1 FROM public.sync_read_snapshot_chunk c,
            LATERAL jsonb_array_elements(c.payload->'items') item
            WHERE c.snapshot_id=NEW.snapshot_id AND c.component='inventory'
              AND (item->>'record_type' IS NULL OR item->>'record_type' NOT IN
                ('inventory_batch','physical_catalog_config','physical_lot','physical_legacy_lot')))
          OR (SELECT count(*) FROM public.sync_read_snapshot_chunk c,
            LATERAL jsonb_array_elements(c.payload->'items') item
            WHERE c.snapshot_id=NEW.snapshot_id AND c.component='inventory')
              <> NEW.inventory_count+NEW.physical_count
          OR EXISTS (
            (SELECT value FROM jsonb_array_elements(NEW.payload)) EXCEPT ALL
            (SELECT item FROM public.sync_read_snapshot_chunk c,
              LATERAL jsonb_array_elements(c.payload->'items') item
              WHERE c.snapshot_id=NEW.snapshot_id AND c.component='inventory'
                AND item->>'record_type' IN
                  ('physical_catalog_config','physical_lot','physical_legacy_lot')))
          OR EXISTS (
            (SELECT item FROM public.sync_read_snapshot_chunk c,
              LATERAL jsonb_array_elements(c.payload->'items') item
              WHERE c.snapshot_id=NEW.snapshot_id AND c.component='inventory'
                AND item->>'record_type' IN
                  ('physical_catalog_config','physical_lot','physical_legacy_lot'))
            EXCEPT ALL (SELECT value FROM jsonb_array_elements(NEW.payload))))
        THEN RAISE EXCEPTION 'Physical read projection does not match immutable chunks'
          USING ERRCODE='23514'; END IF;
      END IF;
"""

STORAGE_GUARD = """
  IF NEW.schema_version IS DISTINCT FROM
    (SELECT schema_version FROM public.sync_read_snapshot WHERE snapshot_id=NEW.snapshot_id)
  THEN RAISE EXCEPTION 'Snapshot component version differs from parent'
    USING ERRCODE='23514'; END IF;
"""

CURSOR_GUARD = """
        IF TG_OP='UPDATE' AND NEW.active_snapshot_id<>OLD.active_snapshot_id AND EXISTS (
          SELECT 1 FROM public.sync_read_snapshot old_snapshot,
            public.sync_read_snapshot new_snapshot
          WHERE old_snapshot.snapshot_id=OLD.active_snapshot_id
            AND new_snapshot.snapshot_id=NEW.active_snapshot_id
            AND old_snapshot.schema_version=2 AND new_snapshot.schema_version<>2)
        THEN RAISE EXCEPTION 'Physical snapshot cannot fall back to legacy history'
          USING ERRCODE='23514'; END IF;
"""


def _definition(signature: str) -> str:
    return str(
        op.get_bind()
        .execute(
            text("SELECT pg_get_functiondef(to_regprocedure(:signature))"), {"signature": signature}
        )
        .scalar_one()
    )


def _patch(signature: str, needle: str, addition: str, *, forward: bool) -> None:
    definition = _definition(signature)
    old, new = (needle, addition + needle) if forward else (addition + needle, needle)
    if definition.count(old) != 1:
        raise RuntimeError("Historical snapshot guard changed unexpectedly")
    op.execute(definition.replace(old, new, 1))


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT {table}_schema_version_check")
        op.execute(
            f"ALTER TABLE public.{table} ADD CONSTRAINT {table}_schema_version_check "
            "CHECK(schema_version IN (1,2))"
        )
    op.execute("""
      ALTER TABLE public.sync_read_projection
        DROP CONSTRAINT sync_read_projection_projection_version_check,
        ADD CONSTRAINT sync_read_projection_projection_version_check
          CHECK(projection_version IN (1,2)),
        ADD COLUMN payload JSONB NOT NULL DEFAULT '[]'::JSONB,
        ADD COLUMN physical_count BIGINT NOT NULL DEFAULT 0,
        ADD CONSTRAINT ck_sync_read_physical_payload CHECK(jsonb_typeof(payload)='array'
          AND physical_count>=0 AND physical_count<=10000
          AND jsonb_array_length(payload)=physical_count AND octet_length(payload::text)<=8388608
          AND (projection_version=2 OR physical_count=0));
    """)
    definition = _definition(HEADER_SIGNATURE)
    if definition.count(INVENTORY_FILTER) != 3:
        raise RuntimeError("Historical inventory projection guard changed unexpectedly")
    op.execute(definition.replace(INVENTORY_FILTER, VERSIONED_FILTER))
    _patch(HEADER_SIGNATURE, "        IF TG_OP = 'DELETE' THEN", PHYSICAL_GUARD, forward=True)
    _patch(STORAGE_SIGNATURE, "  SELECT snapshot.status", STORAGE_GUARD, forward=True)
    _patch(
        "public.trg_guard_sync_read_snapshot_cursor()",
        "        IF TG_OP = 'DELETE' THEN",
        CURSOR_GUARD,
        forward=True,
    )


def downgrade() -> None:
    op.execute(
        "LOCK TABLE public.sync_read_snapshot,public.sync_read_projection IN ACCESS EXCLUSIVE MODE"
    )
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.sync_read_snapshot WHERE schema_version=2)
          OR EXISTS(SELECT 1 FROM public.sync_read_projection
            WHERE projection_version=2 OR physical_count<>0)
        THEN RAISE EXCEPTION 'Physical snapshot downgrade requires empty v2 history'
          USING ERRCODE='55000'; END IF;
      END $$;
    """)
    _patch(
        "public.trg_guard_sync_read_snapshot_cursor()",
        "        IF TG_OP = 'DELETE' THEN",
        CURSOR_GUARD,
        forward=False,
    )
    _patch(STORAGE_SIGNATURE, "  SELECT snapshot.status", STORAGE_GUARD, forward=False)
    _patch(HEADER_SIGNATURE, "        IF TG_OP = 'DELETE' THEN", PHYSICAL_GUARD, forward=False)
    definition = _definition(HEADER_SIGNATURE)
    if definition.count(VERSIONED_FILTER) != 3:
        raise RuntimeError("Physical inventory projection guard changed unexpectedly")
    op.execute(definition.replace(VERSIONED_FILTER, INVENTORY_FILTER))
    op.execute("""
      ALTER TABLE public.sync_read_projection
        DROP CONSTRAINT ck_sync_read_physical_payload,
        DROP COLUMN payload,DROP COLUMN physical_count,
        DROP CONSTRAINT sync_read_projection_projection_version_check,
        ADD CONSTRAINT sync_read_projection_projection_version_check CHECK(projection_version=1)
    """)
    for table in TABLES:
        op.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT {table}_schema_version_check")
        op.execute(
            f"ALTER TABLE public.{table} ADD CONSTRAINT {table}_schema_version_check "
            "CHECK(schema_version=1)"
        )
