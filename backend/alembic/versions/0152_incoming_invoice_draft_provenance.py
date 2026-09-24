"""Allow bounded supplier source name and server-owned invoice provenance in draft rows.

Revision ID: 0152
Revises: 0151
"""

from __future__ import annotations

from alembic import op

revision = "0152"
down_revision = "0151"
branch_labels = None
depends_on = None

_BASE_FIELDS = """
  jsonb_typeof(fields) = 'object'
  AND fields ?& ARRAY[
    'catalog_id','batch_number','manufactured_at','expires_at',
    'qty','purchase_price','sale_price'
  ]
  AND jsonb_typeof(fields->'catalog_id') = 'string'
  AND (fields->>'catalog_id' = '' OR fields->>'catalog_id'
    ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
  AND jsonb_typeof(fields->'batch_number') = 'string'
    AND length(fields->>'batch_number') <= 120
  AND jsonb_typeof(fields->'manufactured_at') = 'string'
    AND length(fields->>'manufactured_at') <= 32
  AND jsonb_typeof(fields->'expires_at') = 'string'
    AND length(fields->>'expires_at') <= 32
  AND jsonb_typeof(fields->'qty') = 'string' AND length(fields->>'qty') <= 64
  AND jsonb_typeof(fields->'purchase_price') = 'string'
    AND length(fields->>'purchase_price') <= 64
  AND jsonb_typeof(fields->'sale_price') = 'string'
    AND length(fields->>'sale_price') <= 64
"""


def upgrade() -> None:
    op.execute("ALTER TABLE public.incoming_draft_row DROP CONSTRAINT ck_incoming_draft_fields")
    op.execute(
        "ALTER TABLE public.incoming_draft_row ADD CONSTRAINT ck_incoming_draft_fields CHECK ("
        + _BASE_FIELDS
        + """
          AND fields - ARRAY[
            'catalog_id','batch_number','manufactured_at','expires_at',
            'qty','purchase_price','sale_price','source_name',
            '_invoice_file_hash','_invoice_mapping_hash','_invoice_operation_id'
          ] = '{}'::jsonb
          AND (NOT fields ? 'source_name' OR (
            jsonb_typeof(fields->'source_name') = 'string'
            AND length(fields->>'source_name') <= 255
          ))
          AND ((NOT fields ? '_invoice_file_hash'
            AND NOT fields ? '_invoice_mapping_hash'
            AND NOT fields ? '_invoice_operation_id') OR (
            fields ?& ARRAY['_invoice_file_hash','_invoice_mapping_hash','_invoice_operation_id']
            AND jsonb_typeof(fields->'_invoice_file_hash') = 'string'
            AND fields->>'_invoice_file_hash' ~ '^[0-9a-f]{64}$'
            AND jsonb_typeof(fields->'_invoice_mapping_hash') = 'string'
            AND fields->>'_invoice_mapping_hash' ~ '^[0-9a-f]{64}$'
            AND jsonb_typeof(fields->'_invoice_operation_id') = 'string'
            AND fields->>'_invoice_operation_id'
              ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
          ))
        )"""
    )


def downgrade() -> None:
    # The prior schema cannot represent imported provenance. It is safer to
    # refuse downgrade while these rows exist than silently discard evidence.
    op.execute("ALTER TABLE public.incoming_draft_row DROP CONSTRAINT ck_incoming_draft_fields")
    op.execute(
        "ALTER TABLE public.incoming_draft_row ADD CONSTRAINT ck_incoming_draft_fields CHECK ("
        + _BASE_FIELDS
        + """
          AND fields - ARRAY[
            'catalog_id','batch_number','manufactured_at','expires_at',
            'qty','purchase_price','sale_price'
          ] = '{}'::jsonb
        )"""
    )
