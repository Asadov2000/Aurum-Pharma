"""Resolve-scoped, read-only recovery of immutable mixed quarantine decisions.

Revision ID: 0194
Revises: 0193
"""

from alembic import op

revision = "0194"
down_revision = "0193"
branch_labels = None
depends_on = None

SIGNATURE = "public.mixed_customer_return_disposition_status(UUID,UUID,UUID)"


def upgrade() -> None:
    op.execute("""
      CREATE FUNCTION public.mixed_customer_return_disposition_status(
        p_tenant UUID,p_branch UUID,p_decision UUID
      ) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp AS $$
      DECLARE v_result JSONB;
      BEGIN
        IF session_user<>'aurum_app' OR p_tenant IS NULL OR p_branch IS NULL
          OR p_decision IS NULL OR p_tenant IS DISTINCT FROM public.current_tenant_id()
          OR coalesce(public.is_support_session(),true)
          OR coalesce(public.is_tenant_support_session(),true)
          OR NOT EXISTS(SELECT 1 FROM public.app_user
            WHERE id=public.current_app_user_id() AND status='active')
          OR NOT coalesce(public.tenant_actor_has_scoped_permission(
            p_tenant,'customer_returns.resolve',p_branch),false) THEN
          RAISE EXCEPTION 'mixed_disposition_status_denied' USING ERRCODE='42501';
        END IF;
        SELECT result_payload INTO v_result FROM public.mixed_customer_return_disposition
          WHERE tenant_id=p_tenant AND branch_id=p_branch AND id=p_decision;
        IF FOUND THEN
          RETURN v_result;
        END IF;
        RETURN jsonb_build_object('state','not_found',
          'decision_operation_id',p_decision,'branch_id',p_branch);
      END $$
    """)
    op.execute(f"ALTER FUNCTION {SIGNATURE} OWNER TO aurum_schema_owner")
    op.execute(f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC,aurum_app,aurum_support")
    op.execute(f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO aurum_app")


def downgrade() -> None:
    # The immutable journal and every original decision remain unchanged.
    op.execute(f"DROP FUNCTION {SIGNATURE}")
