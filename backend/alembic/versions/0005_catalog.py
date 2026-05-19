"""catalog: master_catalog, tenant_catalog, barcode, catalog_import_job

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-19

- master_catalog stays empty in phase 1; it exists for the eventual
  "connected" drug-catalog mode (phase 2). No RLS — globally readable.
- tenant_catalog has trigram GIN indexes on (tenant_id, brand_name) and
  (tenant_id, inn) so the search service can use the `%%` operator.
- import_job_id on tenant_catalog gives the rollback path a way to find
  every row a particular import created, even days later.
- catalog_import_job stores preview_data / errors as JSONB and tracks
  the 24 h rollback window via expires_at_for_rollback.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # master_catalog
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE master_catalog (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          brand_name          TEXT NOT NULL,
          inn                 TEXT,
          manufacturer        TEXT,
          form                TEXT,
          dosage              TEXT,
          pack_size           TEXT,
          atx_code            TEXT,
          dispensing_type     TEXT
                                CHECK (dispensing_type IN ('prescription','otc','special')),
          storage_type        TEXT
                                CHECK (storage_type IN ('normal','cold','frozen')),
          is_active           BOOLEAN NOT NULL DEFAULT true,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_master_catalog_brand_trgm ON master_catalog "
        "USING gin (brand_name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_master_catalog_inn_trgm ON master_catalog "
        "USING gin (inn gin_trgm_ops)"
    )

    # -------------------------------------------------------------------------
    # tenant_catalog
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE tenant_catalog (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id           UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
          master_id           UUID REFERENCES master_catalog(id),
          brand_name          TEXT NOT NULL,
          inn                 TEXT,
          manufacturer        TEXT,
          form                TEXT,
          dosage              TEXT,
          pack_size           TEXT,
          atx_code            TEXT,
          dispensing_type     TEXT NOT NULL DEFAULT 'otc'
                                CHECK (dispensing_type IN ('prescription','otc','special')),
          storage_type        TEXT NOT NULL DEFAULT 'normal'
                                CHECK (storage_type IN ('normal','cold','frozen')),
          category            TEXT,
          base_price          NUMERIC(14, 2),
          currency            TEXT NOT NULL DEFAULT 'TJS'
                                CHECK (currency IN ('TJS')),
          is_active           BOOLEAN NOT NULL DEFAULT true,
          import_job_id       UUID,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by          UUID REFERENCES app_user(id),
          updated_by          UUID REFERENCES app_user(id),
          deleted_at          TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_tc_tenant ON tenant_catalog (tenant_id) WHERE deleted_at IS NULL"
    )
    # GIN over (tenant_id, brand_name) using btree+trgm — needs btree_gin to
    # mix btree and gin in the same index. To stay portable we instead index
    # the trgm column alone and filter by tenant_id in the query plan.
    op.execute(
        "CREATE INDEX ix_tc_brand_trgm ON tenant_catalog "
        "USING gin (brand_name gin_trgm_ops) WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX ix_tc_inn_trgm ON tenant_catalog "
        "USING gin (inn gin_trgm_ops) WHERE deleted_at IS NULL AND inn IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_tc_category ON tenant_catalog (tenant_id, category) "
        "WHERE deleted_at IS NULL AND category IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_tc_import_job ON tenant_catalog (import_job_id) "
        "WHERE import_job_id IS NOT NULL"
    )
    op.execute(
        """
        CREATE TRIGGER trg_tc_updated BEFORE UPDATE ON tenant_catalog
          FOR EACH ROW EXECUTE FUNCTION trg_set_updated_meta()
        """
    )
    op.execute("ALTER TABLE tenant_catalog ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON tenant_catalog
          USING (tenant_id = current_tenant_id() OR is_support_session())
        """
    )

    # -------------------------------------------------------------------------
    # barcode
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE barcode (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id           UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
          catalog_id          UUID NOT NULL REFERENCES tenant_catalog(id) ON DELETE CASCADE,
          code                TEXT NOT NULL,
          code_type           TEXT NOT NULL DEFAULT 'ean13'
                                CHECK (code_type IN ('ean13','ean8','gs1_128','code128','qr','other')),
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, code)
        )
        """
    )
    op.execute("CREATE INDEX ix_barcode_catalog ON barcode (catalog_id)")
    op.execute("ALTER TABLE barcode ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON barcode
          USING (tenant_id = current_tenant_id() OR is_support_session())
        """
    )

    # -------------------------------------------------------------------------
    # catalog_import_job
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE catalog_import_job (
          id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id                UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
          user_id                  UUID NOT NULL REFERENCES app_user(id),
          source_filename          TEXT NOT NULL,
          source_path              TEXT,
          status                   TEXT NOT NULL DEFAULT 'pending'
                                     CHECK (status IN ('pending','validating','importing',
                                                       'success','failed','rolled_back')),
          duplicate_strategy       TEXT NOT NULL DEFAULT 'skip'
                                     CHECK (duplicate_strategy IN ('skip','update','create_copy')),
          total_rows               INT,
          valid_rows               INT,
          error_rows               INT,
          preview_data             JSONB,
          errors                   JSONB,
          created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
          started_at               TIMESTAMPTZ,
          finished_at              TIMESTAMPTZ,
          expires_at_for_rollback  TIMESTAMPTZ,
          rolled_back_at           TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_cij_tenant ON catalog_import_job (tenant_id, created_at DESC)"
    )
    op.execute("ALTER TABLE catalog_import_job ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON catalog_import_job
          USING (tenant_id = current_tenant_id() OR is_support_session())
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS catalog_import_job CASCADE")
    op.execute("DROP TABLE IF EXISTS barcode CASCADE")
    op.execute("DROP TABLE IF EXISTS tenant_catalog CASCADE")
    op.execute("DROP TABLE IF EXISTS master_catalog CASCADE")
