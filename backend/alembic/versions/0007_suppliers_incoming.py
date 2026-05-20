"""suppliers/incoming: supplier, incoming_document, incoming_item, supplier_return

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-19

Tables (all tenant-scoped, RLS enforced):
- supplier            (per-tenant supplier directory)
- incoming_document   (a goods-receipt act from a supplier; lifecycle:
                       draft → accepted | rejected; accept creates batches)
- incoming_item       (line items; on accept each produces one batch
                       and one batch_movement of type 'incoming')
- supplier_return     (return-to-supplier act; service inserts a
                       matching batch_movement of type 'supplier_return'
                       so qty decreases via the inventory trigger)
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- supplier -----------------------------------------------------------
    op.execute(
        """
        CREATE TABLE supplier (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id           UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
          name                TEXT NOT NULL,
          legal_name          TEXT,
          inn_or_tin          TEXT,
          contact_person      TEXT,
          phone               TEXT,
          email               TEXT,
          address             TEXT,
          notes               TEXT,
          is_active           BOOLEAN NOT NULL DEFAULT true,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by          UUID REFERENCES app_user(id),
          updated_by          UUID REFERENCES app_user(id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_supplier_tenant ON supplier (tenant_id) WHERE is_active = true"
    )
    op.execute(
        """
        CREATE TRIGGER trg_supplier_updated BEFORE UPDATE ON supplier
          FOR EACH ROW EXECUTE FUNCTION trg_set_updated_meta()
        """
    )
    op.execute("ALTER TABLE supplier ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON supplier
          USING (tenant_id = current_tenant_id() OR is_support_session())
        """
    )

    # ---- incoming_document --------------------------------------------------
    op.execute(
        """
        CREATE TABLE incoming_document (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id           UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
          branch_id           UUID NOT NULL REFERENCES branch(id),
          supplier_id         UUID NOT NULL REFERENCES supplier(id),
          document_number     TEXT,
          document_date       DATE NOT NULL,
          status              TEXT NOT NULL DEFAULT 'draft'
                                CHECK (status IN ('draft','accepted','rejected')),
          total_amount        NUMERIC(14, 2) NOT NULL DEFAULT 0,
          currency            TEXT NOT NULL DEFAULT 'TJS' CHECK (currency IN ('TJS')),
          notes               TEXT,
          document_file_path  TEXT,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          accepted_at         TIMESTAMPTZ,
          created_by          UUID REFERENCES app_user(id),
          updated_by          UUID REFERENCES app_user(id),
          accepted_by         UUID REFERENCES app_user(id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_id_tenant ON incoming_document (tenant_id, document_date DESC)"
    )
    op.execute("CREATE INDEX ix_id_supplier ON incoming_document (supplier_id)")
    op.execute(
        "CREATE INDEX ix_id_branch ON incoming_document (branch_id, document_date DESC)"
    )
    op.execute(
        """
        CREATE TRIGGER trg_id_updated BEFORE UPDATE ON incoming_document
          FOR EACH ROW EXECUTE FUNCTION trg_set_updated_meta()
        """
    )
    op.execute("ALTER TABLE incoming_document ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON incoming_document
          USING (tenant_id = current_tenant_id() OR is_support_session())
        """
    )

    # ---- incoming_item ------------------------------------------------------
    op.execute(
        """
        CREATE TABLE incoming_item (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id           UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
          document_id         UUID NOT NULL REFERENCES incoming_document(id) ON DELETE CASCADE,
          catalog_id          UUID NOT NULL REFERENCES tenant_catalog(id),
          batch_number        TEXT,
          manufactured_at     DATE,
          expires_at          DATE NOT NULL,
          qty                 NUMERIC(14, 3) NOT NULL CHECK (qty > 0),
          purchase_price      NUMERIC(14, 2) NOT NULL CHECK (purchase_price >= 0),
          sale_price          NUMERIC(14, 2) NOT NULL CHECK (sale_price >= 0),
          currency            TEXT NOT NULL DEFAULT 'TJS' CHECK (currency IN ('TJS')),
          created_batch_id    UUID REFERENCES batch(id),
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_ii_document ON incoming_item (document_id)")
    op.execute("CREATE INDEX ix_ii_catalog ON incoming_item (catalog_id)")
    op.execute(
        """
        CREATE TRIGGER trg_ii_updated BEFORE UPDATE ON incoming_item
          FOR EACH ROW EXECUTE FUNCTION trg_set_updated_meta()
        """
    )
    op.execute("ALTER TABLE incoming_item ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON incoming_item
          USING (tenant_id = current_tenant_id() OR is_support_session())
        """
    )

    # ---- supplier_return ----------------------------------------------------
    op.execute(
        """
        CREATE TABLE supplier_return (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id           UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
          supplier_id         UUID NOT NULL REFERENCES supplier(id),
          source_document_id  UUID REFERENCES incoming_document(id),
          batch_id            UUID NOT NULL REFERENCES batch(id),
          qty                 NUMERIC(14, 3) NOT NULL CHECK (qty > 0),
          amount              NUMERIC(14, 2) NOT NULL,
          currency            TEXT NOT NULL DEFAULT 'TJS' CHECK (currency IN ('TJS')),
          reason              TEXT NOT NULL,
          comment             TEXT,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by          UUID REFERENCES app_user(id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_sr_supplier ON supplier_return (supplier_id, created_at DESC)"
    )
    op.execute("CREATE INDEX ix_sr_batch ON supplier_return (batch_id)")
    op.execute("ALTER TABLE supplier_return ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON supplier_return
          USING (tenant_id = current_tenant_id() OR is_support_session())
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS supplier_return CASCADE")
    op.execute("DROP TABLE IF EXISTS incoming_item CASCADE")
    op.execute("DROP TABLE IF EXISTS incoming_document CASCADE")
    op.execute("DROP TABLE IF EXISTS supplier CASCADE")
