"""One statement snapshot of original payments and their mixed return history."""

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AurumError
from app.domains.pos.mixed_electronic_refund_history_schemas import MixedElectronicRefundHistory

_PAYMENT = """jsonb_build_object(
    'id',p.id,'tenant_id',p.tenant_id,'sale_id',p.sale_id,
    'payment_method',p.payment_method,'amount',p.amount::TEXT,'currency',p.currency,
    'operation_id',p.operation_id,'operation_hash',p.operation_hash,
    'payment_attempt_id',p.payment_attempt_id,'metadata',p.metadata)"""

# Every monetary scalar is projected as text, avoiding JSON float conversion.
# Correlated aggregates share the outer statement's PostgreSQL MVCC snapshot.
_HISTORY = text(f"""
SELECT jsonb_build_object(
  'sale',jsonb_build_object(
    'id',s.id,'tenant_id',s.tenant_id,'branch_id',s.branch_id,
    'register_id',s.register_id,'shift_id',s.shift_id,'cashier_user_id',s.cashier_user_id,
    'parent_sale_id',s.parent_sale_id,'sale_type',s.sale_type,'status',s.status,
    'currency',s.currency,'is_test',s.is_test,'total_amount',s.total_amount::TEXT,
    'created_at',s.created_at,'completed_at',s.completed_at,
    'receipt_number',s.receipt_number,'receipt_seq',s.receipt_seq,
    'receipt_snapshot',s.receipt_snapshot),
  'checkout',jsonb_build_object(
    'id',m.id,'tenant_id',m.tenant_id,'sale_id',m.sale_id,'branch_id',m.branch_id,
    'register_id',m.register_id,'shift_id',m.shift_id,'cashier_user_id',m.cashier_user_id,
    'cash_intent_id',m.cash_intent_id,'payment_plan_intent_id',m.payment_plan_intent_id,
    'stock_command_id',m.stock_command_id,'payment_id',m.payment_id,
    'payment_journal_version',m.payment_journal_version,'request_hash',m.request_hash,
    'result_payload',m.result_payload,'receipt_payload',m.receipt_payload),
  'payments',coalesce((SELECT jsonb_agg({_PAYMENT} ORDER BY p.id)
    FROM public.sale_payment p WHERE p.tenant_id=s.tenant_id AND p.sale_id=s.id),'[]'::JSONB),
  'components',coalesce((SELECT jsonb_agg(jsonb_build_object(
    'id',c.id,'tenant_id',c.tenant_id,'sale_id',c.sale_id,
    'physical_checkout_id',c.physical_checkout_id,'mixed_checkout_id',c.mixed_checkout_id,
    'method',c.method,'amount',c.amount::TEXT,'currency',c.currency,
    'payment_attempt_id',c.payment_attempt_id,'created_by',c.created_by) ORDER BY c.id)
    FROM public.pos_unit_checkout_payment_component c
    WHERE c.tenant_id=s.tenant_id AND c.sale_id=s.id),'[]'::JSONB),
  'return_lines',coalesce((SELECT jsonb_agg(jsonb_build_object(
    'tenant_id',l.tenant_id,'command_id',l.command_id,'line_id',l.line_id,
    'branch_id',l.branch_id,'parent_checkout_id',r.parent_checkout_id,
    'stock_command_id',r.stock_command_id,'inventory_kind',l.inventory_kind,
    'catalog_id',l.catalog_id,'batch_id',l.batch_id,
    'legacy_quantity',l.legacy_quantity::TEXT,'legacy_offset',l.legacy_offset::TEXT,
    'offset',l.offset,'base_units',l.base_units,'sale_format',l.sale_format,
    'quantity',l.quantity,'net_amount',l.net_amount::TEXT,
    'discount_amount',l.discount_amount::TEXT,'gross_amount',l.gross_amount::TEXT)
      ORDER BY l.line_id,coalesce(l.legacy_offset,l.offset),l.command_id)
    FROM public.pos_mixed_return_line l JOIN public.pos_mixed_return_command r
      ON r.tenant_id=l.tenant_id AND r.id=l.command_id
    WHERE r.tenant_id=s.tenant_id AND r.parent_sale_id=s.id),'[]'::JSONB),
  'refund_sales',coalesce((SELECT jsonb_agg(jsonb_build_object(
    'id',r.id,'tenant_id',r.tenant_id,'branch_id',r.branch_id,'parent_sale_id',r.parent_sale_id,
    'status',r.status,'sale_type',r.sale_type,'currency',r.currency,'is_test',r.is_test,
    'total_amount',r.total_amount::TEXT,'cashier_user_id',r.cashier_user_id) ORDER BY r.id)
    FROM public.sale r WHERE r.tenant_id=s.tenant_id AND r.parent_sale_id=s.id
      AND r.status='completed' AND r.sale_type='return'),'[]'::JSONB),
  'payouts',coalesce((SELECT jsonb_agg({_PAYMENT} ORDER BY p.sale_id,p.id)
    FROM public.sale_payment p JOIN public.sale r ON r.tenant_id=p.tenant_id AND r.id=p.sale_id
    WHERE r.tenant_id=s.tenant_id AND r.parent_sale_id=s.id
      AND r.status='completed' AND r.sale_type='return'),'[]'::JSONB),
  'refund_components',coalesce((SELECT jsonb_agg(jsonb_build_object(
    'id',c.id,'tenant_id',c.tenant_id,'parent_sale_id',c.parent_sale_id,'sale_id',c.sale_id,
    'original_payment_id',c.original_payment_id,'physical_refund_id',c.physical_refund_id,
    'mixed_refund_id',c.mixed_refund_id,'electronic_refund_id',c.electronic_refund_id,
    'mixed_electronic_refund_id',c.mixed_electronic_refund_id,
    'method',c.method,'amount',c.amount::TEXT,
    'currency',c.currency,'created_by',c.created_by) ORDER BY c.sale_id,c.id)
    FROM public.pos_unit_refund_payment_component c
    WHERE c.tenant_id=s.tenant_id AND c.parent_sale_id=s.id),'[]'::JSONB)
)
FROM public.sale s JOIN public.pos_mixed_checkout m
  ON m.tenant_id=s.tenant_id AND m.sale_id=s.id
WHERE s.tenant_id=:tenant_id AND s.id=:parent_sale_id
""")


class MixedElectronicRefundPreviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def read(
        self, tenant_id: UUID, parent_sale_id: UUID
    ) -> MixedElectronicRefundHistory | None:
        snapshot: object = await self.session.scalar(
            _HISTORY, {"tenant_id": tenant_id, "parent_sale_id": parent_sale_id}
        )
        if snapshot is None:
            return None
        try:
            return MixedElectronicRefundHistory.model_validate(snapshot)
        except ValidationError:
            raise AurumError("Stored electronic refund history is inconsistent") from None
