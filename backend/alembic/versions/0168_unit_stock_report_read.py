"""Allow scoped report readers to read the immutable unit stock history.

Revision ID: 0168
Revises: 0167
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0168"
down_revision: str | Sequence[str] | None = "0167"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("unit_stock_lot", "unit_stock_entry")


def upgrade() -> None:
    # Report-only users must not receive incoming documents or procurement facts.
    # Return only whether this exact visible lot has an accepted source by cutoff.
    op.execute("""
        CREATE FUNCTION public.unit_lot_is_received_for_report(
            p_tenant UUID, p_branch UUID, p_catalog UUID, p_lot UUID,
            p_cutoff TIMESTAMPTZ
        ) RETURNS BOOLEAN LANGUAGE SQL STABLE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp AS $$
          SELECT coalesce(
            session_user = 'aurum_app' AND NOT public.is_support_session()
            AND public.current_app_user_id() IS NOT NULL
            AND public.current_tenant_id() = p_tenant
            AND public.tenant_actor_has_scoped_permission(p_tenant,'reports.view',p_branch)
            AND EXISTS (
              SELECT 1 FROM public.unit_stock_lot l
              JOIN public.incoming_item i
                ON i.tenant_id=l.tenant_id AND i.created_unit_lot_id=l.id
                AND i.catalog_id=l.catalog_id AND i.created_batch_id IS NULL
              JOIN public.incoming_document d
                ON d.tenant_id=i.tenant_id AND d.id=i.document_id
              WHERE l.tenant_id=p_tenant AND l.branch_id=p_branch
                AND l.catalog_id=p_catalog AND l.id=p_lot
                AND d.branch_id=p_branch AND d.status='accepted'
                AND d.accepted_by IS NOT NULL AND d.accepted_at<p_cutoff
            ), false)
        $$
    """)
    op.execute(
        "ALTER FUNCTION public.unit_lot_is_received_for_report("
        "UUID,UUID,UUID,UUID,TIMESTAMPTZ) OWNER TO aurum_schema_owner"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.unit_lot_is_received_for_report("
        "UUID,UUID,UUID,UUID,TIMESTAMPTZ) FROM PUBLIC,aurum_app,aurum_support"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.unit_lot_is_received_for_report("
        "UUID,UUID,UUID,UUID,TIMESTAMPTZ) TO aurum_app"
    )
    # A separate permissive SELECT policy leaves all existing readers, writers
    # and row-lock policies unchanged. A report grant never permits a mutation.
    for table in TABLES:
        op.execute(
            f"CREATE POLICY tenant_report_read ON public.{table} "
            "FOR SELECT TO aurum_app USING ("
            "tenant_id = public.current_tenant_id() AND "
            "public.tenant_actor_has_scoped_permission("
            "tenant_id, 'reports.view', branch_id))"
        )


def downgrade() -> None:
    # Removing only this read policy preserves every historical stock entry.
    for table in reversed(TABLES):
        op.execute(f"DROP POLICY tenant_report_read ON public.{table}")
    op.execute(
        "DROP FUNCTION public.unit_lot_is_received_for_report(UUID,UUID,UUID,UUID,TIMESTAMPTZ)"
    )
