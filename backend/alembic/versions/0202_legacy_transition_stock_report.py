"""Include transferred closed packs at their transfer time in stock reports.

Revision ID: 0202
Revises: 0201
"""

from alembic import op

revision = "0202"
down_revision = "0201"
branch_labels = None
depends_on = None

ORIGINAL = """AND i.created_unit_lot_v2_id=l.id AND i.catalog_id=l.catalog_id
            AND i.created_batch_id IS NULL AND i.created_unit_lot_id IS NULL"""
TRANSFERRED = """AND i.catalog_id=l.catalog_id AND i.created_unit_lot_id IS NULL
            AND ((i.created_unit_lot_v2_id=l.id AND i.created_batch_id IS NULL)
              OR (i.created_unit_lot_v2_id IS NULL AND EXISTS (
                SELECT 1 FROM public.unit_legacy_transition t
                WHERE t.tenant_id=l.tenant_id AND t.lot_id=l.id
                  AND t.branch_id=l.branch_id AND t.catalog_id=l.catalog_id
                  AND t.source_item_id=i.id AND t.source_document_id=i.document_id
                  AND t.batch_id=i.created_batch_id AND t.created_at<p_cutoff)))"""


def _replace_source(old: str, new: str) -> None:
    # Preserve the established permission and cost-redaction projection verbatim.
    # Both ledger sides use the same timestamp, so every cutoff sees one balance.
    op.execute(
        "DO $migration$ DECLARE definition TEXT; BEGIN "
        "SELECT pg_get_functiondef("
        "'public.pos_stock_on_date_for_report(uuid,timestamptz,uuid)'::regprocedure) "
        "INTO definition; "
        f"IF strpos(definition,$old${old}$old$)=0 THEN "
        "RAISE EXCEPTION 'legacy_transition_report_anchor_missing'; END IF; "
        f"EXECUTE replace(definition,$old${old}$old$,$new${new}$new$); "
        "END $migration$;"
    )


def upgrade() -> None:
    _replace_source(ORIGINAL, TRANSFERRED)


def downgrade() -> None:
    _replace_source(TRANSFERRED, ORIGINAL)
