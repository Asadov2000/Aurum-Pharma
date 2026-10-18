"""Allow scoped refund readers to inspect physical electronic payment history.

Revision ID: 0213
Revises: 0212
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0213"
down_revision: str | Sequence[str] | None = "0212"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY = "physical_electronic_refund_read"


def upgrade() -> None:
    # These SELECT-only policies supplement the existing cashier policies.
    # They never authorize a payment, a payout, or a change to immutable history.
    for table, parent_match in (
        (
            "pos_unit_checkout_payment_component",
            "component.physical_checkout_id=checkout.id "
            "AND component.sale_id=checkout.sale_id "
            "AND component.mixed_checkout_id IS NULL",
        ),
        (
            "pos_unit_refund_payment_component",
            "component.parent_sale_id=checkout.sale_id "
            "AND component.physical_refund_id IS NOT NULL "
            "AND component.mixed_refund_id IS NULL",
        ),
    ):
        parent_match = parent_match.replace("component.", f"{table}.")
        op.execute(
            f"""
            CREATE POLICY {POLICY} ON public.{table} FOR SELECT TO aurum_app
              USING(
                tenant_id=public.current_tenant_id()
                AND current_user='aurum_app' AND session_user='aurum_app'
                AND NOT public.is_support_session()
                AND NOT public.is_tenant_support_session()
                AND EXISTS(SELECT 1 FROM public.app_user actor
                  WHERE actor.id=public.current_app_user_id() AND actor.status='active')
                AND EXISTS(
                  SELECT 1 FROM public.pos_physical_checkout checkout
                  WHERE checkout.tenant_id={table}.tenant_id
                    AND checkout.payment_journal_version=3
                    AND {parent_match}
                    AND public.tenant_actor_has_scoped_permission(
                      checkout.tenant_id,'pos.refund',checkout.branch_id)))
            """
        )


def downgrade() -> None:
    # Removing a read policy preserves all financial facts and existing access.
    for table in ("pos_unit_refund_payment_component", "pos_unit_checkout_payment_component"):
        op.execute(f"DROP POLICY {POLICY} ON public.{table}")
