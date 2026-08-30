"""harden incoming idempotency and inventory tenant integrity

Revision ID: 0127
Revises: 0126
Create Date: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0127"
down_revision: str | Sequence[str] | None = "0126"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "LOCK TABLE public.branch, public.tenant_catalog, public.incoming_document, "
        "public.incoming_item, public.batch, public.batch_movement, public.write_off "
        "IN SHARE ROW EXCLUSIVE MODE"
    )
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM public.incoming_document AS document
            LEFT JOIN public.branch AS branch
              ON branch.tenant_id = document.tenant_id
             AND branch.id = document.branch_id
            WHERE branch.id IS NULL
          ) OR EXISTS (
            SELECT 1
            FROM public.incoming_item AS item
            LEFT JOIN public.incoming_document AS document
              ON document.tenant_id = item.tenant_id
             AND document.id = item.document_id
            LEFT JOIN public.tenant_catalog AS catalog
              ON catalog.tenant_id = item.tenant_id
             AND catalog.id = item.catalog_id
            LEFT JOIN public.batch AS created_batch
              ON created_batch.tenant_id = item.tenant_id
             AND created_batch.id = item.created_batch_id
            WHERE document.id IS NULL
               OR catalog.id IS NULL
               OR (item.created_batch_id IS NOT NULL AND created_batch.id IS NULL)
          ) OR EXISTS (
            SELECT 1
            FROM public.batch AS batch
            LEFT JOIN public.branch AS branch
              ON branch.tenant_id = batch.tenant_id
             AND branch.id = batch.branch_id
            LEFT JOIN public.tenant_catalog AS catalog
              ON catalog.tenant_id = batch.tenant_id
             AND catalog.id = batch.catalog_id
            WHERE branch.id IS NULL OR catalog.id IS NULL
          ) OR EXISTS (
            SELECT 1
            FROM public.batch_movement AS movement
            LEFT JOIN public.batch AS batch
              ON batch.tenant_id = movement.tenant_id
             AND batch.id = movement.batch_id
            WHERE batch.id IS NULL
          ) OR EXISTS (
            SELECT 1
            FROM public.write_off AS write_off
            LEFT JOIN public.batch AS batch
              ON batch.tenant_id = write_off.tenant_id
             AND batch.branch_id = write_off.branch_id
             AND batch.id = write_off.batch_id
            WHERE batch.id IS NULL
          ) THEN
            RAISE EXCEPTION
              'Cannot harden inventory references: a missing or cross-tenant relation exists';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM public.batch_movement
            WHERE qty_delta = 0
               OR (movement_type IN ('incoming', 'sale_return', 'transfer_in') AND qty_delta < 0)
               OR (movement_type IN ('sale', 'write_off', 'supplier_return', 'transfer_out')
                   AND qty_delta > 0)
          ) THEN
            RAISE EXCEPTION
              'Cannot harden inventory movement signs: an invalid quantity direction exists';
          END IF;
        END;
        $$;
        """)

    operation_type = postgresql.UUID(as_uuid=True)
    op.add_column(
        "incoming_document",
        sa.Column("operation_id", operation_type, nullable=True),
    )
    op.add_column(
        "incoming_item",
        sa.Column("operation_id", operation_type, nullable=True),
    )
    op.execute("UPDATE public.incoming_document SET operation_id = gen_random_uuid()")
    op.execute("UPDATE public.incoming_item SET operation_id = gen_random_uuid()")
    op.alter_column("incoming_document", "operation_id", nullable=False)
    op.alter_column("incoming_item", "operation_id", nullable=False)

    op.create_unique_constraint(
        "uq_incoming_document_tenant_id",
        "incoming_document",
        ["tenant_id", "id"],
    )
    op.create_unique_constraint(
        "uq_incoming_document_tenant_operation",
        "incoming_document",
        ["tenant_id", "operation_id"],
    )
    op.create_unique_constraint(
        "uq_incoming_item_tenant_id",
        "incoming_item",
        ["tenant_id", "id"],
    )
    op.create_unique_constraint(
        "uq_incoming_item_tenant_operation",
        "incoming_item",
        ["tenant_id", "operation_id"],
    )
    op.create_unique_constraint(
        "uq_batch_tenant_branch_id",
        "batch",
        ["tenant_id", "branch_id", "id"],
    )

    op.create_foreign_key(
        "fk_id_tenant_branch",
        "incoming_document",
        "branch",
        ["tenant_id", "branch_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_ii_tenant_document",
        "incoming_item",
        "incoming_document",
        ["tenant_id", "document_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_ii_tenant_catalog",
        "incoming_item",
        "tenant_catalog",
        ["tenant_id", "catalog_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_ii_tenant_created_batch",
        "incoming_item",
        "batch",
        ["tenant_id", "created_batch_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_batch_tenant_branch",
        "batch",
        "branch",
        ["tenant_id", "branch_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_batch_tenant_catalog",
        "batch",
        "tenant_catalog",
        ["tenant_id", "catalog_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_bm_tenant_batch",
        "batch_movement",
        "batch",
        ["tenant_id", "batch_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_wo_tenant_branch_batch",
        "write_off",
        "batch",
        ["tenant_id", "branch_id", "batch_id"],
        ["tenant_id", "branch_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_bm_qty_direction",
        "batch_movement",
        "qty_delta <> 0 AND "
        "(movement_type NOT IN ('incoming','sale_return','transfer_in') OR qty_delta > 0) AND "
        "(movement_type NOT IN ('sale','write_off','supplier_return','transfer_out') "
        "OR qty_delta < 0)",
    )
    op.create_check_constraint(
        "ck_id_operation_uuid4",
        "incoming_document",
        "(get_byte(uuid_send(operation_id), 6) >> 4) = 4 "
        "AND (get_byte(uuid_send(operation_id), 8) & 192) = 128",
    )
    op.create_check_constraint(
        "ck_ii_operation_uuid4",
        "incoming_item",
        "(get_byte(uuid_send(operation_id), 6) >> 4) = 4 "
        "AND (get_byte(uuid_send(operation_id), 8) & 192) = 128",
    )


def downgrade() -> None:
    op.drop_constraint("ck_ii_operation_uuid4", "incoming_item", type_="check")
    op.drop_constraint("ck_id_operation_uuid4", "incoming_document", type_="check")
    op.drop_constraint("ck_bm_qty_direction", "batch_movement", type_="check")
    op.drop_constraint("fk_wo_tenant_branch_batch", "write_off", type_="foreignkey")
    op.drop_constraint("fk_bm_tenant_batch", "batch_movement", type_="foreignkey")
    op.drop_constraint("fk_batch_tenant_catalog", "batch", type_="foreignkey")
    op.drop_constraint("fk_batch_tenant_branch", "batch", type_="foreignkey")
    op.drop_constraint("fk_ii_tenant_created_batch", "incoming_item", type_="foreignkey")
    op.drop_constraint("fk_ii_tenant_catalog", "incoming_item", type_="foreignkey")
    op.drop_constraint("fk_ii_tenant_document", "incoming_item", type_="foreignkey")
    op.drop_constraint("fk_id_tenant_branch", "incoming_document", type_="foreignkey")
    op.drop_constraint("uq_batch_tenant_branch_id", "batch", type_="unique")
    op.drop_constraint("uq_incoming_item_tenant_operation", "incoming_item", type_="unique")
    op.drop_constraint("uq_incoming_item_tenant_id", "incoming_item", type_="unique")
    op.drop_constraint(
        "uq_incoming_document_tenant_operation",
        "incoming_document",
        type_="unique",
    )
    op.drop_constraint("uq_incoming_document_tenant_id", "incoming_document", type_="unique")
    op.drop_column("incoming_item", "operation_id")
    op.drop_column("incoming_document", "operation_id")
