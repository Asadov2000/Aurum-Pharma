"""Review persisted electronic refund history without locks, writes or payout attempts."""

import json
from decimal import Decimal, localcontext
from hashlib import sha256
from uuid import UUID

from app.core.errors import AurumError, BusinessRuleError, NotFoundError, PermissionDeniedError
from app.domains.pos.mixed_cash_refund_api_schemas import (
    MixedLegacyRefundOptionLine,
    MixedLegacyRefundQuoteLine,
    MixedPhysicalRefundOptionLine,
    MixedPhysicalRefundQuoteLine,
    MixedRefundOptions,
    MixedRefundQuoteRequest,
)
from app.domains.pos.mixed_documents import LegacyResultLineV4
from app.domains.pos.mixed_electronic_refund_history import (
    original_electronic_checkout,
    require_reconciled_history,
    returned_goods,
    returned_payments,
)
from app.domains.pos.mixed_electronic_refund_preview import (
    MixedElectronicRefundPreview,
)
from app.domains.pos.mixed_electronic_refund_preview_repository import (
    MixedElectronicRefundPreviewRepository,
)
from app.domains.pos.mixed_return_allocation import (
    LegacyReturnAllocation,
    LegacyReturnSelection,
    MixedReturnPlan,
)
from app.domains.pos.mixed_return_journal_repository import MixedReturnJournalRepository
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from app.domains.pos.unit_contracts import EXACT_CONTEXT
from app.domains.pos.unit_documents import UNIT_LABELS
from app.domains.pos.unit_electronic_refund_preview import ElectronicRefundPreviewPayment
from app.domains.pos.unit_payment_refund_allocation import plan_mixed_payment_refund


class MixedElectronicRefundPreviewService:
    def __init__(self, repository: MixedElectronicRefundPreviewRepository) -> None:
        self.repository = repository

    async def options(
        self,
        *,
        tenant_id: UUID,
        parent_sale_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
    ) -> MixedRefundOptions:
        # Match quote's scalar-history boundary: even pending ORM edits must not
        # be flushed while discovering the remaining refundable quantities.
        with self.repository.session.no_autoflush:
            authority = MixedReturnJournalRepository(self.repository.session)
            await authority.assert_identity(tenant_id, actor_id)
            history = await self.repository.read(tenant_id, parent_sale_id)
            if history is None:
                raise NotFoundError("Original mixed electronic checkout not found")
            branch_id = history.sale.branch_id
            if allowed_branch_ids is not None and branch_id not in allowed_branch_ids:
                raise PermissionDeniedError("Electronic refund branch access denied")
            await authority.assert_actor(tenant_id, actor_id, branch_id)
            original = original_electronic_checkout(history, tenant_id, parent_sale_id)
            legacy, physical = returned_goods(history, original)
            payments = returned_payments(history, original)
            require_reconciled_history(history, original, payments)
            reason_mode = await self.repository.read_reason_mode(tenant_id)
            if reason_mode is None:
                raise AurumError("POS settings are unavailable")
            lines: list[MixedLegacyRefundOptionLine | MixedPhysicalRefundOptionLine] = []
            for line in original.lines:
                if isinstance(line, LegacyResultLineV4):
                    returned = legacy.get(line.id, Decimal("0.000"))
                    with localcontext(EXACT_CONTEXT):
                        remaining = line.quantity - returned
                    lines.append(
                        MixedLegacyRefundOptionLine(
                            line_id=line.id,
                            name=line.name,
                            sold_quantity=line.quantity,
                            returned_quantity=returned,
                            remaining_quantity=remaining,
                            original_retail_unit_price=line.original_retail_unit_price,
                        )
                    )
                else:
                    units = physical.get(line.id, 0)
                    lines.append(
                        MixedPhysicalRefundOptionLine(
                            line_id=line.id,
                            name=line.name,
                            unit_label=UNIT_LABELS[line.packaging.unit],
                            packaging=line.packaging,
                            sold_base_units=line.base_units,
                            returned_base_units=units,
                            remaining_base_units=line.base_units - units,
                            original_sale_format=line.sale_format,
                            original_retail_unit_price=line.original_retail_unit_price,
                        )
                    )
            result = MixedRefundOptions(
                parent_sale_id=parent_sale_id,
                branch_id=original.branch_id,
                register_id=original.register_id,
                original_receipt_number=original.receipt_number,
                reason_mode=reason_mode,
                lines=tuple(lines),
            )
            await authority.assert_actor(tenant_id, actor_id, branch_id)
            return result

    async def quote(
        self,
        *,
        tenant_id: UUID,
        parent_sale_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        request: MixedRefundQuoteRequest,
    ) -> MixedElectronicRefundPreview:
        # SQL scalar projections preserve pending ORM edits. A nested transaction
        # would flush those edits even under no_autoflush, so no savepoint is used.
        with self.repository.session.no_autoflush:
            await MixedReturnJournalRepository(self.repository.session).assert_identity(
                tenant_id, actor_id
            )
            history = await self.repository.read(tenant_id, parent_sale_id)
            if history is None:
                raise NotFoundError("Original mixed electronic checkout not found")
            branch_id = history.sale.branch_id
            if allowed_branch_ids is not None and branch_id not in allowed_branch_ids:
                raise PermissionDeniedError("Electronic refund branch access denied")
            authority = MixedReturnJournalRepository(self.repository.session)
            await authority.assert_actor(tenant_id, actor_id, branch_id)
            original = original_electronic_checkout(history, tenant_id, parent_sale_id)
            legacy, physical = returned_goods(history, original)
            payments = returned_payments(history, original)
            require_reconciled_history(history, original, payments)
            selections = tuple(
                (
                    LegacyReturnSelection(UUID(str(item.line_id)), item.quantity)
                    if isinstance(item, LegacyReturnSelection)
                    else PhysicalReturnSelection(
                        UUID(str(item.line_id)), item.sale_format, item.quantity
                    )
                )
                for item in request.selections()
            )
            try:
                plan = plan_mixed_payment_refund(
                    original,
                    selections,
                    returned_legacy_quantities=legacy,
                    returned_base_units=physical,
                    returned_payment_amounts=payments,
                )
            except ValueError:
                raise BusinessRuleError("Choose an available whole refund quantity") from None
            if not isinstance(plan.goods, MixedReturnPlan):
                raise AurumError("Electronic refund allocation is inconsistent")
            payload: dict[str, object] = {"version": 1, "history": history.model_dump(mode="json")}
            payload["selection"] = request.model_dump(mode="json")
            revision = sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            result = MixedElectronicRefundPreview(
                parent_sale_id=parent_sale_id,
                branch_id=branch_id,
                original_receipt_number=original.receipt_number,
                revision_hash=revision,
                total_amount=plan.total_amount,
                lines=tuple(
                    (
                        MixedLegacyRefundQuoteLine(
                            line_id=line.line_id,
                            quantity=line.quantity,
                            offset=line.offset,
                            gross_amount=line.gross_amount,
                            discount_amount=line.discount_amount,
                            net_amount=line.net_amount,
                        )
                        if isinstance(line, LegacyReturnAllocation)
                        else MixedPhysicalRefundQuoteLine(
                            line_id=line.line_id,
                            sale_format=line.sale_format,
                            quantity=line.quantity,
                            base_units=line.base_units,
                            offset=line.offset,
                            gross_amount=line.gross_amount,
                            discount_amount=line.discount_amount,
                            net_amount=line.net_amount,
                        )
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
