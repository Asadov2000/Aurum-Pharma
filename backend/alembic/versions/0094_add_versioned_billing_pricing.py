"""add versioned billing pricing foundation

Revision ID: 0094
Revises: 0093
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0094"
down_revision: str | None = "0093"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PRICE_VERSION_GUARD_SQL = r"""
CREATE FUNCTION public.trg_guard_billing_price_version()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'draft'
      OR NEW.approved_by IS NOT NULL
      OR NEW.approved_at IS NOT NULL
    THEN
      RAISE EXCEPTION 'Billing price versions must be created as unapproved drafts';
    END IF;
    RETURN NEW;
  END IF;

  IF TG_OP = 'DELETE' THEN
    IF OLD.status <> 'draft' THEN
      RAISE EXCEPTION 'Published billing price versions are immutable';
    END IF;
    RETURN OLD;
  END IF;

  IF NEW.id IS DISTINCT FROM OLD.id
    OR NEW.plan_id IS DISTINCT FROM OLD.plan_id
    OR NEW.version_number IS DISTINCT FROM OLD.version_number
    OR NEW.created_at IS DISTINCT FROM OLD.created_at
    OR NEW.created_by IS DISTINCT FROM OLD.created_by
  THEN
    RAISE EXCEPTION 'Billing price version identity is immutable';
  END IF;

  IF OLD.status = 'draft' THEN
    IF NEW.status NOT IN ('draft', 'scheduled') THEN
      RAISE EXCEPTION 'Invalid billing price version transition';
    END IF;
    IF NEW.status = 'draft'
      AND (NEW.approved_by IS NOT NULL OR NEW.approved_at IS NOT NULL)
    THEN
      RAISE EXCEPTION 'Draft billing price versions cannot retain approval';
    END IF;
    IF NEW.status = 'scheduled'
      AND (
        NEW.effective_from IS NULL
        OR NEW.effective_from < pg_catalog.statement_timestamp()
          + pg_catalog.make_interval(days => NEW.notice_days)
      )
    THEN
      RAISE EXCEPTION 'Billing price effective date violates its notice period';
    END IF;
  ELSIF OLD.status = 'scheduled' THEN
    IF NEW.status NOT IN ('scheduled', 'active', 'cancelled') THEN
      RAISE EXCEPTION 'Invalid billing price version transition';
    END IF;
    IF NEW.status = 'active'
      AND (
        pg_catalog.statement_timestamp() < OLD.effective_from
        OR NEW.activated_at IS NULL
        OR NEW.activated_at < OLD.effective_from
        OR NEW.activated_at > pg_catalog.statement_timestamp()
      )
    THEN
      RAISE EXCEPTION 'Billing price cannot be activated before its effective date';
    END IF;
    IF ROW(
      NEW.plan_id,
      NEW.version_number,
      NEW.monthly_price_per_branch,
      NEW.annual_discount_pct,
      NEW.currency,
      NEW.audience,
      NEW.effective_from,
      NEW.notice_days,
      NEW.reason,
      NEW.terms_snapshot,
      NEW.created_by,
      NEW.approved_by,
      NEW.approved_at
    ) IS DISTINCT FROM ROW(
      OLD.plan_id,
      OLD.version_number,
      OLD.monthly_price_per_branch,
      OLD.annual_discount_pct,
      OLD.currency,
      OLD.audience,
      OLD.effective_from,
      OLD.notice_days,
      OLD.reason,
      OLD.terms_snapshot,
      OLD.created_by,
      OLD.approved_by,
      OLD.approved_at
    ) THEN
      RAISE EXCEPTION 'Scheduled billing price terms are immutable';
    END IF;
  ELSIF OLD.status = 'active' THEN
    IF NEW.status NOT IN ('active', 'archived') THEN
      RAISE EXCEPTION 'Invalid billing price version transition';
    END IF;
    IF ROW(
      NEW.plan_id,
      NEW.version_number,
      NEW.monthly_price_per_branch,
      NEW.annual_discount_pct,
      NEW.currency,
      NEW.audience,
      NEW.effective_from,
      NEW.notice_days,
      NEW.reason,
      NEW.terms_snapshot,
      NEW.created_by,
      NEW.approved_by,
      NEW.approved_at,
      NEW.activated_at
    ) IS DISTINCT FROM ROW(
      OLD.plan_id,
      OLD.version_number,
      OLD.monthly_price_per_branch,
      OLD.annual_discount_pct,
      OLD.currency,
      OLD.audience,
      OLD.effective_from,
      OLD.notice_days,
      OLD.reason,
      OLD.terms_snapshot,
      OLD.created_by,
      OLD.approved_by,
      OLD.approved_at,
      OLD.activated_at
    ) THEN
      RAISE EXCEPTION 'Active billing price terms are immutable';
    END IF;
  ELSE
    RAISE EXCEPTION 'Final billing price versions are immutable';
  END IF;

  NEW.updated_at := pg_catalog.statement_timestamp();
  NEW.row_version := OLD.row_version + 1;
  RETURN NEW;
END;
$function$
"""


CONTRACT_OVERRIDE_GUARD_SQL = r"""
CREATE FUNCTION public.trg_guard_billing_contract_override()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'draft'
      OR NEW.approved_by IS NOT NULL
      OR NEW.approved_at IS NOT NULL
    THEN
      RAISE EXCEPTION 'Billing contract overrides must be created as unapproved drafts';
    END IF;
    RETURN NEW;
  END IF;

  IF TG_OP = 'DELETE' THEN
    IF OLD.status <> 'draft' THEN
      RAISE EXCEPTION 'Published billing contract overrides are immutable';
    END IF;
    RETURN OLD;
  END IF;

  IF NEW.id IS DISTINCT FROM OLD.id
    OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
    OR NEW.plan_id IS DISTINCT FROM OLD.plan_id
    OR NEW.created_at IS DISTINCT FROM OLD.created_at
    OR NEW.created_by IS DISTINCT FROM OLD.created_by
  THEN
    RAISE EXCEPTION 'Billing contract override identity is immutable';
  END IF;

  IF OLD.status = 'draft' THEN
    IF NEW.status NOT IN ('draft', 'scheduled') THEN
      RAISE EXCEPTION 'Invalid billing contract override transition';
    END IF;
    IF NEW.status = 'draft'
      AND (NEW.approved_by IS NOT NULL OR NEW.approved_at IS NOT NULL)
    THEN
      RAISE EXCEPTION 'Draft billing contract overrides cannot retain approval';
    END IF;
    IF NEW.status = 'scheduled'
      AND (
        NEW.valid_from IS NULL
        OR NEW.valid_from < pg_catalog.statement_timestamp()
      )
    THEN
      RAISE EXCEPTION 'Billing contract start date must be in the future';
    END IF;
  ELSIF OLD.status = 'scheduled' THEN
    IF NEW.status NOT IN ('scheduled', 'active', 'cancelled') THEN
      RAISE EXCEPTION 'Invalid billing contract override transition';
    END IF;
    IF NEW.status = 'active'
      AND (
        pg_catalog.statement_timestamp() < OLD.valid_from
        OR NEW.activated_at IS NULL
        OR NEW.activated_at < OLD.valid_from
        OR NEW.activated_at > pg_catalog.statement_timestamp()
      )
    THEN
      RAISE EXCEPTION 'Billing contract cannot be activated before its start date';
    END IF;
    IF ROW(
      NEW.monthly_price_per_branch,
      NEW.annual_discount_pct,
      NEW.currency,
      NEW.valid_from,
      NEW.valid_until,
      NEW.reason,
      NEW.terms_snapshot,
      NEW.created_by,
      NEW.approved_by,
      NEW.approved_at
    ) IS DISTINCT FROM ROW(
      OLD.monthly_price_per_branch,
      OLD.annual_discount_pct,
      OLD.currency,
      OLD.valid_from,
      OLD.valid_until,
      OLD.reason,
      OLD.terms_snapshot,
      OLD.created_by,
      OLD.approved_by,
      OLD.approved_at
    ) THEN
      RAISE EXCEPTION 'Scheduled billing contract terms are immutable';
    END IF;
  ELSIF OLD.status = 'active' THEN
    IF NEW.status NOT IN ('active', 'archived') THEN
      RAISE EXCEPTION 'Invalid billing contract override transition';
    END IF;
    IF ROW(
      NEW.monthly_price_per_branch,
      NEW.annual_discount_pct,
      NEW.currency,
      NEW.valid_from,
      NEW.valid_until,
      NEW.reason,
      NEW.terms_snapshot,
      NEW.created_by,
      NEW.approved_by,
      NEW.approved_at,
      NEW.activated_at
    ) IS DISTINCT FROM ROW(
      OLD.monthly_price_per_branch,
      OLD.annual_discount_pct,
      OLD.currency,
      OLD.valid_from,
      OLD.valid_until,
      OLD.reason,
      OLD.terms_snapshot,
      OLD.created_by,
      OLD.approved_by,
      OLD.approved_at,
      OLD.activated_at
    ) THEN
      RAISE EXCEPTION 'Active billing contract terms are immutable';
    END IF;
  ELSE
    RAISE EXCEPTION 'Final billing contract overrides are immutable';
  END IF;

  NEW.updated_at := pg_catalog.statement_timestamp();
  NEW.row_version := OLD.row_version + 1;
  RETURN NEW;
END;
$function$
"""


def _secure_trigger_function(signature: str) -> None:
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} " "FROM PUBLIC, aurum_app, aurum_support"
    )


def _grant_missing_reference_privileges() -> None:
    op.execute("""
        CREATE TEMPORARY TABLE aurum_0094_missing_reference_privilege (
          table_name TEXT PRIMARY KEY
        ) ON COMMIT DROP
        """)
    op.execute("""
        DO $$
        DECLARE
          target_table TEXT;
        BEGIN
          FOREACH target_table IN ARRAY ARRAY['tenant', 'app_user']
          LOOP
            IF NOT pg_catalog.has_table_privilege(
              'aurum_schema_owner',
              pg_catalog.format('public.%I', target_table),
              'REFERENCES'
            ) THEN
              INSERT INTO pg_temp.aurum_0094_missing_reference_privilege (
                table_name
              ) VALUES (target_table);
              EXECUTE pg_catalog.format(
                'GRANT REFERENCES ON TABLE public.%I TO aurum_schema_owner',
                target_table
              );
            END IF;
          END LOOP;
        END
        $$
        """)


def _restore_reference_privileges() -> None:
    op.execute("""
        DO $$
        DECLARE
          target_table TEXT;
        BEGIN
          FOR target_table IN
            SELECT table_name
            FROM pg_temp.aurum_0094_missing_reference_privilege
          LOOP
            EXECUTE pg_catalog.format(
              'REVOKE REFERENCES ON TABLE public.%I FROM aurum_schema_owner',
              target_table
            );
          END LOOP;
        END
        $$
        """)
    op.execute("DROP TABLE pg_temp.aurum_0094_missing_reference_privilege")


def upgrade() -> None:
    _grant_missing_reference_privileges()
    op.execute("""
        CREATE TABLE public.billing_plan (
          id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          code        TEXT NOT NULL,
          name        TEXT NOT NULL,
          description TEXT,
          currency    TEXT NOT NULL DEFAULT 'TJS',
          is_active   BOOLEAN NOT NULL DEFAULT true,
          created_by  UUID NOT NULL REFERENCES public.app_user(id) ON DELETE RESTRICT,
          updated_by  UUID REFERENCES public.app_user(id) ON DELETE RESTRICT,
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_billing_plan_code UNIQUE (code),
          CONSTRAINT ck_billing_plan_code CHECK (
            code ~ '^[a-z][a-z0-9_]{2,63}$'
          ),
          CONSTRAINT ck_billing_plan_name CHECK (
            char_length(btrim(name)) BETWEEN 2 AND 160
          ),
          CONSTRAINT ck_billing_plan_description CHECK (
            description IS NULL OR char_length(description) <= 2000
          ),
          CONSTRAINT ck_billing_plan_currency CHECK (currency = 'TJS')
        )
        """)

    op.execute("""
        CREATE TABLE public.billing_price_version (
          id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          plan_id                  UUID NOT NULL
                                     REFERENCES public.billing_plan(id) ON DELETE RESTRICT,
          version_number           INTEGER NOT NULL,
          status                   TEXT NOT NULL DEFAULT 'draft',
          monthly_price_per_branch NUMERIC(14,2) NOT NULL,
          annual_discount_pct      NUMERIC(5,2) NOT NULL DEFAULT 20.00,
          currency                 TEXT NOT NULL DEFAULT 'TJS',
          audience                 TEXT NOT NULL DEFAULT 'default',
          effective_from           TIMESTAMPTZ,
          notice_days              SMALLINT NOT NULL DEFAULT 30,
          reason                   TEXT,
          terms_snapshot           JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_by               UUID NOT NULL
                                     REFERENCES public.app_user(id) ON DELETE RESTRICT,
          approved_by              UUID REFERENCES public.app_user(id) ON DELETE RESTRICT,
          approved_at              TIMESTAMPTZ,
          activated_at             TIMESTAMPTZ,
          archived_at              TIMESTAMPTZ,
          created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
          row_version              INTEGER NOT NULL DEFAULT 1,
          CONSTRAINT uq_billing_price_plan_version
            UNIQUE (plan_id, version_number),
          CONSTRAINT uq_billing_price_effective_start
            UNIQUE NULLS NOT DISTINCT (plan_id, audience, effective_from),
          CONSTRAINT ck_billing_price_version_number CHECK (version_number > 0),
          CONSTRAINT ck_billing_price_status CHECK (
            status IN ('draft','scheduled','active','archived','cancelled')
          ),
          CONSTRAINT ck_billing_price_amount CHECK (monthly_price_per_branch >= 0),
          CONSTRAINT ck_billing_price_discount CHECK (
            annual_discount_pct >= 0 AND annual_discount_pct < 100
          ),
          CONSTRAINT ck_billing_price_currency CHECK (currency = 'TJS'),
          CONSTRAINT ck_billing_price_audience CHECK (
            audience IN ('default','new_customers')
          ),
          CONSTRAINT ck_billing_price_notice_days CHECK (
            notice_days BETWEEN 0 AND 365
            AND (audience = 'new_customers' OR notice_days >= 30)
          ),
          CONSTRAINT ck_billing_price_reason CHECK (
            reason IS NULL OR char_length(btrim(reason)) BETWEEN 3 AND 1000
          ),
          CONSTRAINT ck_billing_price_terms_snapshot CHECK (
            jsonb_typeof(terms_snapshot) = 'object'
            AND octet_length(terms_snapshot::text) BETWEEN 2 AND 65536
          ),
          CONSTRAINT ck_billing_price_approval_pair CHECK (
            (approved_by IS NULL) = (approved_at IS NULL)
          ),
          CONSTRAINT ck_billing_price_separation CHECK (
            approved_by IS NULL OR approved_by <> created_by
          ),
          CONSTRAINT ck_billing_price_lifecycle CHECK (
            (status = 'draft' AND approved_by IS NULL AND approved_at IS NULL
              AND activated_at IS NULL AND archived_at IS NULL)
            OR
            (status = 'scheduled' AND effective_from IS NOT NULL
              AND reason IS NOT NULL AND approved_by IS NOT NULL
              AND activated_at IS NULL AND archived_at IS NULL)
            OR
            (status = 'active' AND effective_from IS NOT NULL
              AND reason IS NOT NULL AND approved_by IS NOT NULL
              AND activated_at IS NOT NULL AND archived_at IS NULL)
            OR
            (status = 'archived' AND effective_from IS NOT NULL
              AND reason IS NOT NULL AND approved_by IS NOT NULL
              AND activated_at IS NOT NULL AND archived_at IS NOT NULL)
            OR
            (status = 'cancelled' AND effective_from IS NOT NULL
              AND reason IS NOT NULL AND approved_by IS NOT NULL
              AND activated_at IS NULL AND archived_at IS NULL)
          ),
          CONSTRAINT ck_billing_price_timestamps CHECK (
            (approved_at IS NULL OR approved_at >= created_at)
            AND (activated_at IS NULL OR activated_at >= approved_at)
            AND (archived_at IS NULL OR archived_at >= activated_at)
          ),
          CONSTRAINT ck_billing_price_row_version CHECK (row_version > 0)
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_billing_price_one_active_audience
        ON public.billing_price_version (plan_id, audience)
        WHERE status = 'active'
        """)
    op.execute("""
        CREATE INDEX ix_billing_price_schedule
        ON public.billing_price_version (status, effective_from)
        WHERE status IN ('scheduled','active')
        """)

    op.execute("""
        CREATE TABLE public.billing_contract_override (
          id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id                UUID NOT NULL
                                     REFERENCES public.tenant(id) ON DELETE RESTRICT,
          plan_id                  UUID NOT NULL
                                     REFERENCES public.billing_plan(id) ON DELETE RESTRICT,
          status                   TEXT NOT NULL DEFAULT 'draft',
          monthly_price_per_branch NUMERIC(14,2) NOT NULL,
          annual_discount_pct      NUMERIC(5,2) NOT NULL DEFAULT 20.00,
          currency                 TEXT NOT NULL DEFAULT 'TJS',
          valid_from               TIMESTAMPTZ,
          valid_until              TIMESTAMPTZ,
          reason                   TEXT,
          terms_snapshot           JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_by               UUID NOT NULL
                                     REFERENCES public.app_user(id) ON DELETE RESTRICT,
          approved_by              UUID REFERENCES public.app_user(id) ON DELETE RESTRICT,
          approved_at              TIMESTAMPTZ,
          activated_at             TIMESTAMPTZ,
          archived_at              TIMESTAMPTZ,
          created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
          row_version              INTEGER NOT NULL DEFAULT 1,
          CONSTRAINT uq_billing_contract_override_tenant_id
            UNIQUE (tenant_id, id),
          CONSTRAINT uq_billing_contract_override_start
            UNIQUE NULLS NOT DISTINCT (tenant_id, plan_id, valid_from),
          CONSTRAINT ck_billing_contract_override_status CHECK (
            status IN ('draft','scheduled','active','archived','cancelled')
          ),
          CONSTRAINT ck_billing_contract_override_amount CHECK (
            monthly_price_per_branch >= 0
          ),
          CONSTRAINT ck_billing_contract_override_discount CHECK (
            annual_discount_pct >= 0 AND annual_discount_pct < 100
          ),
          CONSTRAINT ck_billing_contract_override_currency CHECK (currency = 'TJS'),
          CONSTRAINT ck_billing_contract_override_dates CHECK (
            valid_until IS NULL OR valid_until > valid_from
          ),
          CONSTRAINT ck_billing_contract_override_reason CHECK (
            reason IS NULL OR char_length(btrim(reason)) BETWEEN 3 AND 1000
          ),
          CONSTRAINT ck_billing_contract_override_terms CHECK (
            jsonb_typeof(terms_snapshot) = 'object'
            AND octet_length(terms_snapshot::text) BETWEEN 2 AND 65536
          ),
          CONSTRAINT ck_billing_contract_override_approval_pair CHECK (
            (approved_by IS NULL) = (approved_at IS NULL)
          ),
          CONSTRAINT ck_billing_contract_override_separation CHECK (
            approved_by IS NULL OR approved_by <> created_by
          ),
          CONSTRAINT ck_billing_contract_override_lifecycle CHECK (
            (status = 'draft' AND approved_by IS NULL AND approved_at IS NULL
              AND activated_at IS NULL AND archived_at IS NULL)
            OR
            (status = 'scheduled' AND valid_from IS NOT NULL
              AND reason IS NOT NULL AND approved_by IS NOT NULL
              AND activated_at IS NULL AND archived_at IS NULL)
            OR
            (status = 'active' AND valid_from IS NOT NULL
              AND reason IS NOT NULL AND approved_by IS NOT NULL
              AND activated_at IS NOT NULL AND archived_at IS NULL)
            OR
            (status = 'archived' AND valid_from IS NOT NULL
              AND reason IS NOT NULL AND approved_by IS NOT NULL
              AND activated_at IS NOT NULL AND archived_at IS NOT NULL)
            OR
            (status = 'cancelled' AND valid_from IS NOT NULL
              AND reason IS NOT NULL AND approved_by IS NOT NULL
              AND activated_at IS NULL AND archived_at IS NULL)
          ),
          CONSTRAINT ck_billing_contract_override_timestamps CHECK (
            (approved_at IS NULL OR approved_at >= created_at)
            AND (activated_at IS NULL OR activated_at >= approved_at)
            AND (archived_at IS NULL OR archived_at >= activated_at)
          ),
          CONSTRAINT ck_billing_contract_override_row_version CHECK (row_version > 0)
        )
        """)
    _restore_reference_privileges()

    op.execute("""
        CREATE UNIQUE INDEX uq_billing_contract_one_active_plan
        ON public.billing_contract_override (tenant_id, plan_id)
        WHERE status = 'active'
        """)
    op.execute("""
        CREATE INDEX ix_billing_contract_override_schedule
        ON public.billing_contract_override (status, valid_from)
        WHERE status IN ('scheduled','active')
        """)

    op.execute(PRICE_VERSION_GUARD_SQL)
    _secure_trigger_function("public.trg_guard_billing_price_version()")
    op.execute("""
        CREATE TRIGGER trg_guard_billing_price_version
        BEFORE INSERT OR UPDATE OR DELETE ON public.billing_price_version
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_billing_price_version()
        """)

    op.execute(CONTRACT_OVERRIDE_GUARD_SQL)
    _secure_trigger_function("public.trg_guard_billing_contract_override()")
    op.execute("""
        CREATE TRIGGER trg_guard_billing_contract_override
        BEFORE INSERT OR UPDATE OR DELETE ON public.billing_contract_override
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_billing_contract_override()
        """)
    op.execute("""
        CREATE TRIGGER trg_audit_billing_contract_override
        AFTER INSERT OR UPDATE OR DELETE ON public.billing_contract_override
        FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log()
        """)

    op.execute("ALTER TABLE public.billing_contract_override ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.billing_contract_override FORCE ROW LEVEL SECURITY")
    op.execute("""
        REVOKE ALL PRIVILEGES ON TABLE
          public.billing_plan,
          public.billing_price_version,
          public.billing_contract_override
        FROM PUBLIC, aurum_app, aurum_support, aurum_mailer,
          aurum_edge_cash_executor, aurum_edge_cash_owner
        """)


def downgrade() -> None:
    op.execute("""
        DO $guard$
        BEGIN
          IF EXISTS (SELECT 1 FROM public.billing_contract_override)
            OR EXISTS (SELECT 1 FROM public.billing_price_version)
            OR EXISTS (SELECT 1 FROM public.billing_plan)
          THEN
            RAISE EXCEPTION
              'Refusing to remove non-empty versioned billing pricing tables';
          END IF;
        END
        $guard$
        """)
    op.execute("DROP TABLE public.billing_contract_override")
    op.execute("DROP FUNCTION public.trg_guard_billing_contract_override()")
    op.execute("DROP TABLE public.billing_price_version")
    op.execute("DROP FUNCTION public.trg_guard_billing_price_version()")
    op.execute("DROP TABLE public.billing_plan")
