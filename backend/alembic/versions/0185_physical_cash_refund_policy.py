"""Persist refund reasons and bind reviewed physical history without rewriting receipts.

Revision ID: 0185
Revises: 0184
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0185"
down_revision: str | Sequence[str] | None = "0184"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace(old: str, new: str) -> None:
    definition = op.get_bind().scalar(
        text("SELECT pg_get_functiondef('public.trg_physical_cash_refund_guard()'::regprocedure)")
    )
    if not isinstance(definition, str) or definition.count(old) != 1:
        raise RuntimeError("Physical refund policy guard differs from expected definition")
    op.get_bind().exec_driver_sql(definition.replace(old, new, 1))


DECLARATION_OLD = " v_payload TEXT; v_completed TIMESTAMPTZ;"
DECLARATION_NEW = DECLARATION_OLD + """
 v_reason_mode TEXT;
 -- Match Python str.strip() rather than trimming ASCII space alone.
 v_trim TEXT:=chr(9)||chr(10)||chr(11)||chr(12)||chr(13)||chr(28)||chr(29)
   ||chr(30)||chr(31)||chr(32)||chr(133)||chr(160)||chr(5760)
   ||chr(8192)||chr(8193)||chr(8194)||chr(8195)||chr(8196)||chr(8197)
   ||chr(8198)||chr(8199)||chr(8200)||chr(8201)||chr(8202)
   ||chr(8232)||chr(8233)||chr(8239)||chr(8287)||chr(12288);"""
HASH_OLD = (
    """ v_payload:='{"actor_id":"'||NEW.actor_id||'","journal_request_hash":"'||c.request_hash
   ||'","kind":"physical_cash_refund","parent_sale_id":"'||p.id
   ||'","shift_id":"'||NEW.shift_id||'","tenant_id":"'||NEW.tenant_id||'","version":3}';"""
)
HASH_NEW = (
    """ SELECT refund_reason_mode INTO v_reason_mode FROM public.tenant_settings
   WHERE tenant_id=NEW.tenant_id FOR SHARE;
 IF v_reason_mode IS NULL
   OR v_reason_mode NOT IN ('off','optional','required','required_with_text')
   OR (NEW.reason_code IS NOT NULL AND NEW.reason_code NOT IN (
     'dispensing_error','duplicate_sale','pricing_error','quality_issue',
     'damaged_package','customer_cancelled','other'))
   OR (NEW.comment IS NOT NULL AND (char_length(NEW.comment) NOT BETWEEN 1 AND 500
     OR NEW.comment IS DISTINCT FROM btrim(NEW.comment,v_trim)))
   OR (NEW.review_hash IS NOT NULL AND NEW.review_hash !~ '^[0-9a-f]{64}$')
   OR (v_reason_mode='off' AND (NEW.reason_code IS NOT NULL OR NEW.comment IS NOT NULL))
   OR (v_reason_mode IN ('required','required_with_text') AND NEW.reason_code IS NULL)
   OR (v_reason_mode='required_with_text' AND NEW.comment IS NULL) THEN
  RAISE EXCEPTION 'physical_cash_refund_reason_invalid' USING ERRCODE='23514';
 END IF;
 IF NEW.reason_code IS NULL AND NEW.comment IS NULL AND NEW.review_hash IS NULL THEN
"""
    + HASH_OLD
    + """
 ELSE
  v_payload:='{"actor_id":"'||NEW.actor_id||'","comment_hash":'
    ||CASE WHEN NEW.comment IS NULL THEN 'null'
      ELSE '"'||encode(sha256(convert_to(NEW.comment,'UTF8')),'hex')||'"' END
    ||',"journal_request_hash":"'||c.request_hash
    ||'","kind":"physical_cash_refund","parent_sale_id":"'||p.id||'","reason_code":'
    ||CASE WHEN NEW.reason_code IS NULL THEN 'null' ELSE '"'||NEW.reason_code||'"' END
    ||',"review_hash":'
    ||CASE WHEN NEW.review_hash IS NULL THEN 'null' ELSE '"'||NEW.review_hash||'"' END
    ||',"shift_id":"'||NEW.shift_id||'","tenant_id":"'||NEW.tenant_id||'","version":4}';
 END IF;"""
)


def upgrade() -> None:
    op.execute("""
      ALTER TABLE public.pos_physical_cash_refund
        ADD COLUMN reason_code TEXT,
        ADD COLUMN comment TEXT,
        ADD COLUMN review_hash TEXT,
        ADD CONSTRAINT ck_physical_cash_refund_reason CHECK (
          reason_code IS NULL OR reason_code IN (
            'dispensing_error','duplicate_sale','pricing_error','quality_issue',
            'damaged_package','customer_cancelled','other')),
        ADD CONSTRAINT ck_physical_cash_refund_comment CHECK (
          comment IS NULL OR char_length(comment) BETWEEN 1 AND 500),
        ADD CONSTRAINT ck_physical_cash_refund_review CHECK (
          review_hash IS NULL OR review_hash ~ '^[0-9a-f]{64}$')
    """)
    _replace(DECLARATION_OLD, DECLARATION_NEW)
    _replace(HASH_OLD, HASH_NEW)


def downgrade() -> None:
    op.execute("LOCK TABLE public.pos_physical_cash_refund IN ACCESS EXCLUSIVE MODE")
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.pos_physical_cash_refund
          WHERE reason_code IS NOT NULL OR comment IS NOT NULL OR review_hash IS NOT NULL) THEN
          RAISE EXCEPTION 'physical_cash_refund_policy_history_exists' USING ERRCODE='55000';
        END IF;
      END $$
    """)
    _replace(HASH_NEW, HASH_OLD)
    _replace(DECLARATION_NEW, DECLARATION_OLD)
    op.execute("""
      ALTER TABLE public.pos_physical_cash_refund
        DROP CONSTRAINT ck_physical_cash_refund_review,
        DROP CONSTRAINT ck_physical_cash_refund_comment,
        DROP CONSTRAINT ck_physical_cash_refund_reason,
        DROP COLUMN review_hash,
        DROP COLUMN comment,
        DROP COLUMN reason_code
    """)
