"""Review persisted electronic refund history without locks, writes or payout attempts."""

import json
from hashlib import sha256
from uuid import UUID

from app.core.errors import AurumError, BusinessRuleError, NotFoundError, PermissionDeniedError
from app.domains.pos.physical_cash_refund_api_schemas import (
    PhysicalRefundQuoteLine,
    PhysicalRefundQuoteRequest,
)
from app.domains.pos.physical_return_allocation import PhysicalReturnPlan, PhysicalReturnSelection
from app.domains.pos.physical_return_journal_repository import PhysicalReturnJournalRepository
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_electronic_refund_history import (
    original_electronic_checkout,
    require_reconciled_history,
    returned_goods,
    returned_payments,
)
from app.domains.pos.unit_electronic_refund_preview import (
    ElectronicRefundPreviewPayment,
    UnitElectronicRefundPreview,
)
from app.domains.pos.unit_electronic_refund_preview_repository import (
    UnitElectronicRefundPreviewRepository,
)
from app.domains.pos.unit_payment_refund_allocation import plan_physical_payment_refund


class UnitElectronicRefundPreviewService:
    def __init__(self, repository: UnitElectronicRefundPreviewRepository) -> None:
        self.repository = repository

    async def quote(
        self,
        *,
        tenant_id: UUID,
        parent_sale_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        request: PhysicalRefundQuoteRequest,
    ) -> UnitElectronicRefundPreview:
        # SQL scalar projections preserve pending ORM edits. A nested transaction
        # would flush those edits even under no_autoflush, so no savepoint is used.
        with self.repository.session.no_autoflush:
            await UnitCartRepository(self.repository.session).assert_actor(tenant_id, actor_id)
            history = await self.repository.read(tenant_id, parent_sale_id)
            if history is None:
                raise NotFoundError("Original physical electronic checkout not found")
            branch_id = history.sale.branch_id
            if allowed_branch_ids is not None and branch_id not in allowed_branch_ids:
                raise PermissionDeniedError("Electronic refund branch access denied")
            authority = PhysicalReturnJournalRepository(self.repository.session)
            await authority.assert_actor(tenant_id, actor_id, branch_id)
            original = original_electronic_checkout(history, tenant_id, parent_sale_id)
            goods = returned_goods(history, original)
            payments = returned_payments(history, original)
            require_reconciled_history(original, goods, payments)
            # Driver UUID subclasses can survive Pydantic's Python input path.
            # The pure allocator deliberately requires canonical UUID values.
            selections = tuple(
                PhysicalReturnSelection(UUID(str(item.line_id)), item.sale_format, item.quantity)
                for item in request.items
            )
            try:
                plan = plan_physical_payment_refund(
                    original,
                    selections,
                    returned_base_units=goods,
                    returned_payment_amounts=payments,
                )
            except ValueError:
                raise BusinessRuleError("Choose an available whole refund quantity") from None
            if not isinstance(plan.goods, PhysicalReturnPlan):
                raise AurumError("Electronic refund allocation is inconsistent")
            payload: dict[str, object] = {"version": 1, "history": history.model_dump(mode="json")}
            payload["selection"] = request.model_dump(mode="json")
            revision = sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            result = UnitElectronicRefundPreview(
                parent_sale_id=parent_sale_id,
                branch_id=branch_id,
                original_receipt_number=original.receipt_number,
                revision_hash=revision,
                total_amount=plan.total_amount,
                lines=tuple(
                    PhysicalRefundQuoteLine(
                        line_id=line.line_id,
                        sale_format=line.sale_format,
                        quantity=line.quantity,
                        base_units=line.base_units,
                        offset=line.offset,
                        gross_amount=line.gross_amount,
                        discount_amount=line.discount_amount,
                        net_amount=line.net_amount,
                    )
                    for line in plan.goods.lines
                ),
                payments=tuple(
                    ElectronicRefundPreviewPayment(
                        original_payment_id=part.original_payment_id,
                        method=part.method,
                        amount=part.amount,
                    )
                    for part in plan.payments
                ),
            )
            await authority.assert_actor(tenant_id, actor_id, branch_id)
            return result
