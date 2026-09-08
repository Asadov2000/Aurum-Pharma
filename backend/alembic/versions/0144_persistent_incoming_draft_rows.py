"""Preserve unfinished receipt input without weakening inventory constraints."""

from alembic import op

revision = "0144"
down_revision = "0143"
branch_labels = None
depends_on = None


def _execute_sql(sql: str) -> None:
    """Execute this static, line-delimited SQL one statement at a time for asyncpg."""
    lines: list[str] = []
    in_body = False
    for line in sql.splitlines():
        lines.append(line)
        if line.count("$$") % 2:
            in_body = not in_body
        if not in_body and line.rstrip().endswith(";"):
            op.execute("\n".join(lines))
            lines = []
    if "\n".join(lines).strip():
        raise ValueError("Unterminated static migration statement")


def _audit_redactor(*, drafts: bool) -> str:
    draft_case = (
        "ELSIF p_table_name = 'incoming_draft_row' THEN v_field := 'fields';" if drafts else ""
    )
    return f"""
    CREATE OR REPLACE FUNCTION public.audit_redact_table_snapshot(p_table_name TEXT, p_data JSONB)
    RETURNS JSONB LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER
    SET search_path = pg_catalog, pg_temp AS $$
    DECLARE v_result JSONB := public.audit_redact_jsonb(p_data); v_field TEXT;
    BEGIN
      IF v_result IS NULL THEN RETURN NULL; END IF;
      IF p_table_name = 'incoming_document' THEN v_field := 'total_amount';
      ELSIF p_table_name = 'supplier_return' THEN v_field := 'amount';
      {draft_case}
      ELSE RETURN v_result; END IF;
      IF v_result ? v_field AND v_result -> v_field <> 'null'::JSONB THEN
        v_result := pg_catalog.jsonb_set(
          v_result, ARRAY[v_field], pg_catalog.to_jsonb('***'::TEXT), false
        );
      END IF;
      RETURN v_result;
    END;
    $$;
    """


def upgrade() -> None:
    _execute_sql("""
      CREATE TABLE public.incoming_draft_row (
        tenant_id UUID NOT NULL,
        id UUID NOT NULL,
        document_id UUID NOT NULL,
        revision INTEGER NOT NULL CHECK (revision BETWEEN 1 AND 2147483646),
        status TEXT NOT NULL DEFAULT 'pending'
          CHECK (status IN ('pending','committed','discarded')),
        fields JSONB NOT NULL,
        committed_item_id UUID,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_by UUID,
        updated_by UUID,
        PRIMARY KEY (tenant_id, id),
        CONSTRAINT ck_incoming_draft_uuid4 CHECK (
          substring(id::text,15,1) = '4' AND substring(id::text,20,1) IN ('8','9','a','b')
        ),
        CONSTRAINT fk_incoming_draft_document FOREIGN KEY (tenant_id,document_id)
          REFERENCES public.incoming_document(tenant_id,id) ON DELETE RESTRICT,
        CONSTRAINT ck_incoming_draft_committed CHECK (
          (status = 'committed') = (committed_item_id IS NOT NULL)
        ),
        CONSTRAINT ck_incoming_draft_fields CHECK (
          jsonb_typeof(fields) = 'object'
          AND fields ?& ARRAY[
            'catalog_id','batch_number','manufactured_at','expires_at',
            'qty','purchase_price','sale_price'
          ]
          AND fields - ARRAY[
            'catalog_id','batch_number','manufactured_at','expires_at',
            'qty','purchase_price','sale_price'
          ] = '{}'::jsonb
          AND jsonb_typeof(fields->'catalog_id') = 'string'
          AND (fields->>'catalog_id' = '' OR fields->>'catalog_id'
            ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
          AND jsonb_typeof(fields->'batch_number') = 'string'
            AND length(fields->>'batch_number') <= 120
          AND jsonb_typeof(fields->'manufactured_at') = 'string'
            AND length(fields->>'manufactured_at') <= 32
          AND jsonb_typeof(fields->'expires_at') = 'string' AND length(fields->>'expires_at') <= 32
          AND jsonb_typeof(fields->'qty') = 'string' AND length(fields->>'qty') <= 64
          AND jsonb_typeof(fields->'purchase_price') = 'string'
            AND length(fields->>'purchase_price') <= 64
          AND jsonb_typeof(fields->'sale_price') = 'string' AND length(fields->>'sale_price') <= 64
        )
      );
      CREATE INDEX ix_incoming_draft_pending
        ON public.incoming_draft_row(tenant_id,document_id,created_at,id)
        WHERE status = 'pending';
      ALTER TABLE public.incoming_draft_row OWNER TO aurum_schema_owner;
      ALTER TABLE public.incoming_draft_row ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.incoming_draft_row FORCE ROW LEVEL SECURITY;
      CREATE POLICY tenant_isolation ON public.incoming_draft_row
        USING (tenant_id = public.current_tenant_id())
        WITH CHECK (tenant_id = public.current_tenant_id());
      REVOKE ALL ON public.incoming_draft_row FROM PUBLIC,aurum_app,aurum_support;
      GRANT SELECT,INSERT,UPDATE ON public.incoming_draft_row TO aurum_app,aurum_support;
    """)
    _execute_sql("""
      CREATE FUNCTION public.trg_guard_incoming_draft_row() RETURNS TRIGGER
      LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
      DECLARE v_status TEXT; v_count INTEGER;
      BEGIN
        IF TG_OP = 'DELETE' THEN
          RAISE EXCEPTION 'Draft row tombstones cannot be deleted' USING ERRCODE='check_violation';
        END IF;
        IF TG_OP = 'UPDATE' AND (
          NEW.id IS DISTINCT FROM OLD.id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
          OR NEW.document_id IS DISTINCT FROM OLD.document_id
          OR NEW.created_at IS DISTINCT FROM OLD.created_at
          OR NEW.created_by IS DISTINCT FROM OLD.created_by OR OLD.status <> 'pending'
          OR NEW.revision <> OLD.revision + 1
        ) THEN
          RAISE EXCEPTION 'Draft row identity or terminal state cannot change'
            USING ERRCODE='check_violation';
        END IF;
        SELECT status INTO v_status FROM public.incoming_document
          WHERE id=NEW.document_id AND tenant_id=NEW.tenant_id FOR UPDATE;
        IF NOT FOUND OR v_status <> 'draft' THEN
          RAISE EXCEPTION 'Draft rows require a matching draft document'
            USING ERRCODE='check_violation';
        END IF;
        IF TG_OP = 'INSERT' THEN
          IF NEW.revision <> 1 OR NEW.status <> 'pending' THEN
            RAISE EXCEPTION 'Draft row must start pending at revision one'
              USING ERRCODE='check_violation';
          END IF;
          SELECT count(*) INTO v_count FROM public.incoming_draft_row
            WHERE document_id=NEW.document_id AND tenant_id=NEW.tenant_id AND status='pending';
          IF v_count >= 500 THEN
            RAISE EXCEPTION 'Too many pending draft rows' USING ERRCODE='check_violation';
          END IF;
        END IF;
        IF TG_OP = 'UPDATE' AND NEW.status <> 'pending'
          AND NEW.fields IS DISTINCT FROM OLD.fields THEN
          RAISE EXCEPTION 'Closing a draft row cannot change its fields'
            USING ERRCODE='check_violation';
        END IF;
        IF NEW.status = 'committed' AND NOT EXISTS (
          SELECT 1 FROM public.incoming_item
          WHERE id=NEW.committed_item_id AND tenant_id=NEW.tenant_id
            AND document_id=NEW.document_id AND operation_id=NEW.id
        ) THEN
          RAISE EXCEPTION 'Committed draft row requires its matching incoming item'
            USING ERRCODE='check_violation';
        END IF;
        RETURN NEW;
      END;
      $$;
      CREATE FUNCTION public.trg_guard_incoming_pending_rows() RETURNS TRIGGER
      LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
      BEGIN
        IF NEW.status = 'accepted' AND EXISTS (
          SELECT 1 FROM public.incoming_draft_row
          WHERE tenant_id=NEW.tenant_id AND document_id=NEW.id AND status='pending'
        ) THEN
          RAISE EXCEPTION 'Pending draft rows prevent acceptance' USING ERRCODE='check_violation';
        END IF;
        RETURN NEW;
      END;
      $$;
    """)
    for function in ("trg_guard_incoming_draft_row", "trg_guard_incoming_pending_rows"):
        op.execute(f"ALTER FUNCTION public.{function}() OWNER TO aurum_schema_owner")
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{function}() FROM PUBLIC,aurum_app,aurum_support"
        )
    op.execute(_audit_redactor(drafts=True))
    _execute_sql("""
      CREATE TRIGGER trg_incoming_draft_guard
        BEFORE INSERT OR UPDATE OR DELETE ON public.incoming_draft_row
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_incoming_draft_row();
      CREATE TRIGGER trg_incoming_draft_updated BEFORE UPDATE ON public.incoming_draft_row
        FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_meta();
      CREATE TRIGGER trg_incoming_draft_audit
        AFTER INSERT OR UPDATE OR DELETE ON public.incoming_draft_row
        FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log();
      CREATE TRIGGER trg_incoming_pending_guard BEFORE UPDATE OF status ON public.incoming_document
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_incoming_pending_rows();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_incoming_pending_guard ON public.incoming_document")
    op.execute("DROP TABLE public.incoming_draft_row")
    op.execute("DROP FUNCTION public.trg_guard_incoming_pending_rows()")
    op.execute("DROP FUNCTION public.trg_guard_incoming_draft_row()")
    op.execute(_audit_redactor(drafts=False))
