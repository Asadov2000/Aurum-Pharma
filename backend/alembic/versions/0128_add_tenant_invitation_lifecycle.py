"""add versioned tenant employee invitation lifecycle

Revision ID: 0128
Revises: 0127
Create Date: 2026-08-30
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0128"
down_revision: str | Sequence[str] | None = "0127"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REFERENCE_TABLES = ("tenant", "tenant_membership", "app_user")


def _load_revision(filename: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(f"aurum_migration_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load migration source: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _secure_function(signature: str, *, app_access: bool = False) -> None:
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_schema_owner")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} " "FROM PUBLIC, aurum_app, aurum_support"
    )
    if app_access:
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO aurum_app, aurum_support")


def _grant_missing_reference_privileges() -> None:
    op.execute("""
        CREATE TEMPORARY TABLE aurum_0128_missing_reference_privilege (
          table_name TEXT PRIMARY KEY
        ) ON COMMIT DROP
        """)
    for table_name in REFERENCE_TABLES:
        op.execute(f"""
            DO $$
            BEGIN
              IF NOT pg_catalog.has_table_privilege(
                'aurum_schema_owner', 'public.{table_name}', 'REFERENCES'
              ) THEN
                INSERT INTO pg_temp.aurum_0128_missing_reference_privilege (table_name)
                VALUES ('{table_name}');
                GRANT REFERENCES ON TABLE public.{table_name} TO aurum_schema_owner;
              END IF;
            END
            $$
            """)


def _restore_reference_privileges() -> None:
    for table_name in REFERENCE_TABLES:
        op.execute(f"""
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1 FROM pg_temp.aurum_0128_missing_reference_privilege
                WHERE table_name = '{table_name}'
              ) THEN
                REVOKE REFERENCES ON TABLE public.{table_name} FROM aurum_schema_owner;
              END IF;
            END
            $$
            """)
    op.execute("DROP TABLE pg_temp.aurum_0128_missing_reference_privilege")


INVITATION_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_tenant_invitation()
RETURNS TRIGGER AS $$
BEGIN
  IF session_user NOT IN ('aurum_app', 'aurum_support') THEN
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
  END IF;

  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Tenant invitation history is immutable'
      USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'INSERT' THEN
    RETURN NEW;
  END IF;

  IF OLD.id IS DISTINCT FROM NEW.id
    OR OLD.tenant_id IS DISTINCT FROM NEW.tenant_id
    OR OLD.membership_id IS DISTINCT FROM NEW.membership_id
    OR OLD.user_id IS DISTINCT FROM NEW.user_id
    OR OLD.version IS DISTINCT FROM NEW.version
    OR OLD.operation_id IS DISTINCT FROM NEW.operation_id
    OR OLD.issued_at IS DISTINCT FROM NEW.issued_at
    OR OLD.expires_at IS DISTINCT FROM NEW.expires_at
    OR OLD.created_at IS DISTINCT FROM NEW.created_at
    OR OLD.created_by IS DISTINCT FROM NEW.created_by
  THEN
    RAISE EXCEPTION 'Tenant invitation contents are immutable'
      USING ERRCODE = '42501';
  END IF;

  IF OLD.status = 'pending'
    AND NEW.status IN ('accepted', 'revoked')
    AND (
      (NEW.status = 'accepted' AND OLD.accepted_at IS NULL AND NEW.accepted_at IS NOT NULL
        AND NEW.revoked_at IS NULL)
      OR
      (NEW.status = 'revoked' AND OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL
        AND NEW.accepted_at IS NULL)
    )
  THEN
    RETURN NEW;
  END IF;

  RAISE EXCEPTION 'Tenant invitation transition is not allowed'
    USING ERRCODE = '42501';
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


BIND_EMAIL_CODE_SQL = """
CREATE FUNCTION public.trg_bind_tenant_invitation_email_code()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.purpose <> 'login' THEN RETURN NEW; END IF;

  SELECT invitation.id
  INTO NEW.tenant_invitation_id
  FROM public.app_user AS account
  JOIN public.tenant_membership AS membership
    ON membership.user_id = account.id
   AND membership.tenant_id = account.home_tenant_id
  JOIN public.tenant_invitation AS invitation
    ON invitation.membership_id = membership.id
   AND invitation.tenant_id = membership.tenant_id
   AND invitation.user_id = membership.user_id
  WHERE account.email_lower = NEW.email_lower
    AND account.status = 'invited'
    AND membership.status = 'pending'
    AND invitation.status = 'pending'
    AND invitation.expires_at > pg_catalog.statement_timestamp()
  ORDER BY invitation.version DESC
  LIMIT 1;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


GUARD_EMAIL_OUTBOX_SQL = """
CREATE FUNCTION public.trg_guard_tenant_invitation_email_delivery()
RETURNS TRIGGER AS $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM public.email_code AS code
    JOIN public.app_user AS account ON account.email_lower = code.email_lower
    WHERE code.id = NEW.email_code_id
      AND account.status = 'invited'
      AND NOT EXISTS (
        SELECT 1
        FROM public.tenant_invitation AS invitation
        WHERE invitation.id = code.tenant_invitation_id
          AND invitation.status = 'pending'
          AND invitation.expires_at > pg_catalog.statement_timestamp()
      )
  ) THEN
    RETURN NULL;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


MATCH_EMAIL_CODE_SQL = """
CREATE OR REPLACE FUNCTION public.auth_email_code_matches(
  p_code_id UUID,
  p_email TEXT,
  p_candidate_hash TEXT
) RETURNS BOOLEAN AS $$
  SELECT EXISTS (
    SELECT 1
    FROM public.email_code AS code
    WHERE code.id = p_code_id
      AND code.email_lower = pg_catalog.lower(pg_catalog.btrim(p_email))
      AND code.purpose = 'login'
      AND code.code_hash = p_candidate_hash
      AND code.used_at IS NULL
      AND code.expires_at > pg_catalog.now()
      AND (
        code.tenant_invitation_id IS NULL
        OR EXISTS (
          SELECT 1
          FROM public.tenant_invitation AS invitation
          WHERE invitation.id = code.tenant_invitation_id
            AND invitation.status = 'pending'
            AND invitation.expires_at > pg_catalog.now()
        )
      )
  )
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


REISSUE_SQL = """
CREATE FUNCTION public.reissue_tenant_invitation(
  p_tenant_id UUID,
  p_user_id UUID,
  p_operation_id UUID,
  p_issued_at TIMESTAMPTZ
) RETURNS TABLE(
  invitation_id UUID,
  invitation_status TEXT,
  invited_at TIMESTAMPTZ,
  invitation_expires_at TIMESTAMPTZ
) AS $$
DECLARE
  v_actor_id UUID := public.current_app_user_id();
  v_membership_id UUID;
  v_version INTEGER;
  v_invitation_id UUID;
  v_existing_user_id UUID;
  v_now TIMESTAMPTZ := COALESCE(p_issued_at, pg_catalog.statement_timestamp());
BEGIN
  IF p_tenant_id IS NULL OR p_user_id IS NULL OR p_operation_id IS NULL THEN
    RAISE EXCEPTION 'Invitation request is incomplete' USING ERRCODE = '22004';
  END IF;
  IF NOT public.tenant_actor_is_owner(p_tenant_id)
    OR NOT public.tenant_actor_has_permission(p_tenant_id, 'users.invite')
  THEN
    RAISE EXCEPTION 'Invitation reissue is not allowed' USING ERRCODE = '42501';
  END IF;

  SELECT invitation.id, invitation.user_id, invitation.status,
         invitation.issued_at, invitation.expires_at
  INTO invitation_id, v_existing_user_id, invitation_status,
       invited_at, invitation_expires_at
  FROM public.tenant_invitation AS invitation
  WHERE invitation.tenant_id = p_tenant_id
    AND invitation.operation_id = p_operation_id;
  IF invitation_id IS NOT NULL THEN
    IF v_existing_user_id IS DISTINCT FROM p_user_id THEN
      RAISE EXCEPTION 'Invitation operation belongs to another employee'
        USING ERRCODE = '22023';
    END IF;
    RETURN NEXT; RETURN;
  END IF;

  SELECT membership.id
  INTO v_membership_id
  FROM public.tenant_membership AS membership
  WHERE membership.tenant_id = p_tenant_id
    AND membership.user_id = p_user_id
    AND membership.status = 'pending'
  FOR UPDATE;
  IF v_membership_id IS NULL THEN
    RAISE EXCEPTION 'Only a pending membership can receive an invitation'
      USING ERRCODE = '42501';
  END IF;

  -- A concurrent retry can only become visible after the membership lock is
  -- acquired. Re-read the idempotency key before creating another version.
  SELECT invitation.id, invitation.user_id, invitation.status,
         invitation.issued_at, invitation.expires_at
  INTO invitation_id, v_existing_user_id, invitation_status,
       invited_at, invitation_expires_at
  FROM public.tenant_invitation AS invitation
  WHERE invitation.tenant_id = p_tenant_id
    AND invitation.operation_id = p_operation_id;
  IF invitation_id IS NOT NULL THEN
    IF v_existing_user_id IS DISTINCT FROM p_user_id THEN
      RAISE EXCEPTION 'Invitation operation belongs to another employee'
        USING ERRCODE = '22023';
    END IF;
    RETURN NEXT; RETURN;
  END IF;

  UPDATE public.tenant_invitation AS invitation
  SET status = 'revoked', revoked_at = v_now
  WHERE invitation.membership_id = v_membership_id
    AND invitation.status = 'pending';

  UPDATE public.email_code AS code
  SET used_at = COALESCE(code.used_at, v_now)
  WHERE code.tenant_invitation_id IN (
    SELECT invitation.id
    FROM public.tenant_invitation AS invitation
    WHERE invitation.membership_id = v_membership_id
      AND invitation.status = 'revoked'
  ) AND code.used_at IS NULL;

  UPDATE public.auth_email_outbox AS delivery
  SET status = 'cancelled', payload_ciphertext = NULL,
      claim_token = NULL, claimed_at = NULL, updated_at = v_now
  FROM public.email_code AS code
  WHERE delivery.email_code_id = code.id
    AND code.tenant_invitation_id IN (
      SELECT invitation.id FROM public.tenant_invitation AS invitation
      WHERE invitation.membership_id = v_membership_id
        AND invitation.status = 'revoked'
    )
    AND delivery.status IN ('pending', 'processing');

  SELECT COALESCE(pg_catalog.max(invitation.version), 0) + 1
  INTO v_version
  FROM public.tenant_invitation AS invitation
  WHERE invitation.membership_id = v_membership_id;

  INSERT INTO public.tenant_invitation (
    tenant_id, membership_id, user_id, version, status, operation_id,
    issued_at, expires_at, created_by
  ) VALUES (
    p_tenant_id, v_membership_id, p_user_id, v_version, 'pending', p_operation_id,
    v_now, v_now + INTERVAL '7 days', v_actor_id
  ) RETURNING id INTO v_invitation_id;

  UPDATE public.tenant_membership AS membership
  SET invited_at = v_now, updated_by = v_actor_id
  WHERE membership.id = v_membership_id;

  invitation_id := v_invitation_id;
  invitation_status := 'pending';
  invited_at := v_now;
  invitation_expires_at := v_now + INTERVAL '7 days';
  RETURN NEXT;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ACCEPT_SQL = """
CREATE FUNCTION public.accept_tenant_invitation(
  p_session_id UUID,
  p_tenant_id UUID,
  p_email_code_id UUID,
  p_accepted_at TIMESTAMPTZ
) RETURNS INTEGER AS $$
DECLARE
  v_user_id UUID;
  v_home_tenant_id UUID;
  v_membership_id UUID;
  v_invitation_id UUID;
BEGIN
  IF p_accepted_at IS NULL OR p_email_code_id IS NULL THEN
    RAISE EXCEPTION 'Invitation proof is required' USING ERRCODE = '22004';
  END IF;

  SELECT auth_session.user_id, account.home_tenant_id
  INTO v_user_id, v_home_tenant_id
  FROM public.session AS auth_session
  JOIN public.app_user AS account ON account.id = auth_session.user_id
  WHERE auth_session.id = p_session_id
    AND auth_session.revoked_at IS NULL;
  IF v_user_id IS NULL OR p_tenant_id IS NULL
    OR v_home_tenant_id IS DISTINCT FROM p_tenant_id
  THEN RETURN 0; END IF;

  SELECT membership.id, invitation.id
  INTO v_membership_id, v_invitation_id
  FROM public.email_code AS code
  JOIN public.tenant_invitation AS invitation
    ON invitation.id = code.tenant_invitation_id
  JOIN public.tenant_membership AS membership
    ON membership.id = invitation.membership_id
   AND membership.tenant_id = invitation.tenant_id
   AND membership.user_id = invitation.user_id
  JOIN public.app_user AS account
    ON account.id = membership.user_id
   AND account.email_lower = code.email_lower
  WHERE code.id = p_email_code_id
    AND code.used_at IS NOT NULL
    AND membership.tenant_id = p_tenant_id
    AND membership.user_id = v_user_id
    AND membership.status = 'pending'
    AND invitation.status = 'pending'
    AND invitation.expires_at > p_accepted_at
  FOR UPDATE OF membership, invitation;
  IF v_invitation_id IS NULL THEN RETURN 0; END IF;

  PERFORM pg_catalog.set_config('app.user_id', v_user_id::TEXT, true);
  UPDATE public.tenant_invitation
  SET status = 'accepted', accepted_at = p_accepted_at
  WHERE id = v_invitation_id AND status = 'pending';
  IF NOT FOUND THEN RETURN 0; END IF;

  UPDATE public.tenant_membership
  SET status = 'active', activated_at = COALESCE(activated_at, p_accepted_at),
      suspended_at = NULL, updated_by = v_user_id
  WHERE id = v_membership_id AND status = 'pending';
  IF NOT FOUND THEN RETURN 0; END IF;

  UPDATE public.app_user
  SET status = 'active', activated_at = COALESCE(activated_at, p_accepted_at)
  WHERE id = v_user_id AND status = 'invited';
  RETURN 1;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


SET_MEMBERSHIP_STATUS_SQL = """
CREATE OR REPLACE FUNCTION public.set_tenant_membership_status(
  p_tenant_id UUID,
  p_user_id UUID,
  p_status TEXT,
  p_changed_at TIMESTAMPTZ
) RETURNS BOOLEAN AS $$
DECLARE
  v_actor_id UUID := public.current_app_user_id();
  v_current_status TEXT;
  v_required_permission TEXT;
  v_updated INTEGER;
BEGIN
  IF p_changed_at IS NULL THEN
    RAISE EXCEPTION 'Status timestamp is required' USING ERRCODE = '22004';
  END IF;
  IF p_status = 'active' THEN v_required_permission := 'users.update';
  ELSIF p_status = 'suspended' THEN v_required_permission := 'users.block';
  ELSIF p_status = 'offboarded' THEN v_required_permission := 'users.delete';
  ELSE RAISE EXCEPTION 'Unsupported membership status transition' USING ERRCODE = '22023';
  END IF;
  IF NOT public.tenant_actor_is_owner(p_tenant_id)
    OR NOT public.tenant_actor_has_permission(p_tenant_id, v_required_permission)
  THEN RAISE EXCEPTION 'Membership status update is not allowed' USING ERRCODE = '42501';
  END IF;

  SELECT status INTO v_current_status
  FROM public.tenant_membership
  WHERE tenant_id = p_tenant_id AND user_id = p_user_id
  FOR UPDATE;
  IF v_current_status IS NULL OR p_user_id = v_actor_id OR v_current_status = 'offboarded'
    OR (p_status = 'active' AND v_current_status <> 'suspended')
    OR (p_status = 'suspended' AND v_current_status <> 'active')
  THEN RAISE EXCEPTION 'Membership status transition is not allowed' USING ERRCODE = '42501';
  END IF;
  IF NOT public.is_support_session() AND EXISTS (
    SELECT 1 FROM public.tenant_ownership AS ownership
    JOIN public.tenant_membership AS membership
      ON membership.id = ownership.membership_id AND membership.tenant_id = ownership.tenant_id
    WHERE ownership.tenant_id = p_tenant_id AND membership.user_id = p_user_id
      AND ownership.is_active
  ) THEN
    RAISE EXCEPTION 'An owner membership cannot be changed through user lifecycle'
      USING ERRCODE = '42501';
  END IF;

  UPDATE public.tenant_membership
  SET status = p_status,
      activated_at = CASE WHEN p_status = 'active' THEN COALESCE(activated_at, p_changed_at)
                          ELSE activated_at END,
      suspended_at = CASE WHEN p_status = 'suspended' THEN p_changed_at
                          WHEN p_status = 'active' THEN NULL ELSE suspended_at END,
      offboarded_at = CASE WHEN p_status = 'offboarded' THEN p_changed_at ELSE offboarded_at END,
      updated_by = v_actor_id
  WHERE tenant_id = p_tenant_id AND user_id = p_user_id;
  GET DIAGNOSTICS v_updated = ROW_COUNT;

  IF v_updated = 1 AND p_status = 'offboarded' THEN
    UPDATE public.tenant_invitation
    SET status = 'revoked', revoked_at = p_changed_at
    WHERE tenant_id = p_tenant_id AND user_id = p_user_id AND status = 'pending';
    UPDATE public.email_code AS code
    SET used_at = COALESCE(code.used_at, p_changed_at)
    WHERE code.tenant_invitation_id IN (
      SELECT id FROM public.tenant_invitation
      WHERE tenant_id = p_tenant_id AND user_id = p_user_id AND status = 'revoked'
    ) AND code.used_at IS NULL;
    UPDATE public.user_assignment
    SET is_active = false, updated_by = v_actor_id
    WHERE tenant_id = p_tenant_id AND user_id = p_user_id AND is_active;
  END IF;
  IF v_updated = 1 AND p_status IN ('suspended', 'offboarded') THEN
    UPDATE public.session
    SET revoked_at = COALESCE(revoked_at, p_changed_at),
        revoked_reason = COALESCE(revoked_reason, 'membership_' || p_status)
    WHERE user_id = p_user_id AND revoked_at IS NULL;
  END IF;
  RETURN v_updated = 1;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


def upgrade() -> None:
    _grant_missing_reference_privileges()
    op.execute("""
        CREATE TABLE public.tenant_invitation (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES public.tenant(id) ON DELETE RESTRICT,
          membership_id UUID NOT NULL REFERENCES public.tenant_membership(id) ON DELETE RESTRICT,
          user_id UUID NOT NULL REFERENCES public.app_user(id) ON DELETE RESTRICT,
          version INTEGER NOT NULL CHECK (version >= 1),
          status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending','accepted','revoked')),
          operation_id UUID NOT NULL,
          issued_at TIMESTAMPTZ NOT NULL,
          expires_at TIMESTAMPTZ NOT NULL CHECK (expires_at > issued_at),
          accepted_at TIMESTAMPTZ,
          revoked_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          created_by UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          CONSTRAINT uq_tenant_invitation_membership_version UNIQUE (membership_id, version),
          CONSTRAINT uq_tenant_invitation_tenant_operation UNIQUE (tenant_id, operation_id),
          CONSTRAINT ck_tenant_invitation_resolution CHECK (
            (status = 'pending' AND accepted_at IS NULL AND revoked_at IS NULL)
            OR (status = 'accepted' AND accepted_at IS NOT NULL AND revoked_at IS NULL)
            OR (status = 'revoked' AND accepted_at IS NULL AND revoked_at IS NOT NULL)
          )
        )
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_tenant_invitation_membership_pending
        ON public.tenant_invitation (membership_id) WHERE status = 'pending'
        """)
    op.execute(
        "CREATE INDEX ix_tenant_invitation_tenant_status "
        "ON public.tenant_invitation (tenant_id, status)"
    )
    op.execute("ALTER TABLE public.email_code ADD COLUMN tenant_invitation_id UUID")
    op.execute("""
        ALTER TABLE public.email_code ADD CONSTRAINT fk_email_code_tenant_invitation
        FOREIGN KEY (tenant_invitation_id) REFERENCES public.tenant_invitation(id)
        ON DELETE SET NULL
        """)
    op.execute(
        "CREATE INDEX ix_email_code_tenant_invitation "
        "ON public.email_code (tenant_invitation_id)"
    )

    op.execute("""
        INSERT INTO public.tenant_invitation (
          tenant_id, membership_id, user_id, version, status, operation_id,
          issued_at, expires_at, created_by
        )
        SELECT membership.tenant_id, membership.id, membership.user_id, 1, 'pending',
               gen_random_uuid(), statement_timestamp(),
               statement_timestamp() + INTERVAL '7 days', membership.created_by
        FROM public.tenant_membership AS membership
        WHERE membership.status = 'pending'
        """)

    op.execute("ALTER TABLE public.tenant_invitation ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.tenant_invitation FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_invitation_read ON public.tenant_invitation
        FOR SELECT TO aurum_app USING (tenant_id = public.current_tenant_id())
        """)
    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.tenant_invitation "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute("GRANT SELECT ON TABLE public.tenant_invitation TO aurum_app")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.tenant_invitation " "TO aurum_support"
    )

    op.execute(INVITATION_GUARD_SQL)
    op.execute(BIND_EMAIL_CODE_SQL)
    op.execute(GUARD_EMAIL_OUTBOX_SQL)
    op.execute(MATCH_EMAIL_CODE_SQL)
    op.execute(REISSUE_SQL)
    op.execute("DROP FUNCTION public.accept_tenant_invitation(UUID, UUID, TIMESTAMPTZ)")
    op.execute(ACCEPT_SQL)
    op.execute(SET_MEMBERSHIP_STATUS_SQL)

    for signature, app_access in (
        ("public.trg_guard_tenant_invitation()", False),
        ("public.trg_bind_tenant_invitation_email_code()", False),
        ("public.trg_guard_tenant_invitation_email_delivery()", False),
        ("public.auth_email_code_matches(UUID, TEXT, TEXT)", True),
        ("public.reissue_tenant_invitation(UUID, UUID, UUID, TIMESTAMPTZ)", True),
        ("public.accept_tenant_invitation(UUID, UUID, UUID, TIMESTAMPTZ)", True),
        ("public.set_tenant_membership_status(UUID, UUID, TEXT, TIMESTAMPTZ)", True),
    ):
        _secure_function(signature, app_access=app_access)

    op.execute("""
        CREATE TRIGGER trg_guard_tenant_invitation
        BEFORE INSERT OR UPDATE OR DELETE ON public.tenant_invitation
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_tenant_invitation()
        """)
    op.execute("""
        CREATE TRIGGER trg_audit_tenant_invitation
        AFTER INSERT OR UPDATE OR DELETE ON public.tenant_invitation
        FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log()
        """)
    op.execute("""
        CREATE TRIGGER trg_bind_tenant_invitation_email_code
        BEFORE INSERT ON public.email_code
        FOR EACH ROW EXECUTE FUNCTION public.trg_bind_tenant_invitation_email_code()
        """)
    op.execute("""
        CREATE TRIGGER trg_guard_tenant_invitation_email_delivery
        BEFORE INSERT ON public.auth_email_outbox
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_tenant_invitation_email_delivery()
        """)
    _restore_reference_privileges()


def downgrade() -> None:
    source_0053 = _load_revision("0053_add_scoped_delegated_authorization.py")
    source_0037 = _load_revision("0037_harden_auth_state_access.py")

    op.execute(
        "DROP TRIGGER trg_guard_tenant_invitation_email_delivery " "ON public.auth_email_outbox"
    )
    op.execute("DROP TRIGGER trg_bind_tenant_invitation_email_code ON public.email_code")
    op.execute("DROP TRIGGER trg_audit_tenant_invitation ON public.tenant_invitation")
    op.execute("DROP TRIGGER trg_guard_tenant_invitation ON public.tenant_invitation")
    op.execute("DROP FUNCTION public.reissue_tenant_invitation(UUID, UUID, UUID, TIMESTAMPTZ)")
    op.execute("DROP FUNCTION public.accept_tenant_invitation(UUID, UUID, UUID, TIMESTAMPTZ)")
    op.execute(source_0053.ACCEPT_TENANT_INVITATION_SQL)
    op.execute(
        source_0053.SET_MEMBERSHIP_STATUS_SQL.replace(
            "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
        )
    )
    op.execute(
        source_0037.AUTH_EMAIL_CODE_MATCHES_SQL.replace(
            "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
        )
    )
    _secure_function("public.accept_tenant_invitation(UUID, UUID, TIMESTAMPTZ)", app_access=True)
    _secure_function(
        "public.set_tenant_membership_status(UUID, UUID, TEXT, TIMESTAMPTZ)",
        app_access=True,
    )
    _secure_function("public.auth_email_code_matches(UUID, TEXT, TEXT)", app_access=True)
    op.execute("DROP FUNCTION public.trg_guard_tenant_invitation_email_delivery()")
    op.execute("DROP FUNCTION public.trg_bind_tenant_invitation_email_code()")
    op.execute("DROP FUNCTION public.trg_guard_tenant_invitation()")
    op.execute("DROP INDEX public.ix_email_code_tenant_invitation")
    op.execute("ALTER TABLE public.email_code DROP CONSTRAINT fk_email_code_tenant_invitation")
    op.execute("ALTER TABLE public.email_code DROP COLUMN tenant_invitation_id")
    op.execute("DROP TABLE public.tenant_invitation")
