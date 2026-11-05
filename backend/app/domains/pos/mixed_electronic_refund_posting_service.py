"""Atomically post a reconciled physical refund; no terminal or bank invocation."""

from uuid import UUID, uuid4

from pydantic import ValidationError

from app.core.errors import AurumError, BusinessRuleError, ConflictError, NotFoundError
from app.core.time import utc_now
from app.domains.pos.mixed_cash_refund_schemas import MixedCashRefundLineV4
from app.domains.pos.mixed_cash_refund_service import _receipt as mixed_cash_receipt
from app.domains.pos.mixed_documents import MixedCheckoutResultV4
from app.domains.pos.mixed_electronic_refund_binding_repository import (
    MixedElectronicRefundBindingRepository,
)
from app.domains.pos.mixed_electronic_refund_intent_repository import (
    MixedElectronicRefundIntentRepository,
)
from app.domains.pos.mixed_electronic_refund_outcome_repository import (
    MixedElectronicRefundOutcomeRepository,
)
from app.domains.pos.mixed_electronic_refund_outcome_schemas import ReconciledMixedElectronicRefund
from app.domains.pos.mixed_electronic_refund_outcome_service import (
    MixedElectronicRefundOutcomeService,
)
from app.domains.pos.mixed_electronic_refund_posting_models import MixedElectronicRefundPosting
from app.domains.pos.mixed_electronic_refund_posting_repository import (
    MixedElectronicRefundPostingRepository,
)
from app.domains.pos.mixed_electronic_refund_posting_schemas import (
    MixedElectronicRefundReceiptV4,
    PostedMixedElectronicRefund,
)
from app.domains.pos.mixed_electronic_refund_revalidation import (
    MixedElectronicRefundRevalidationService,
)
from app.domains.pos.mixed_return_journal_repository import MixedReturnJournalRepository
from app.domains.pos.mixed_return_journal_schemas import MixedReturnJournalResult
from app.domains.pos.mixed_return_journal_service import MixedReturnJournalService
from app.domains.pos.models import Sale
from app.domains.pos.physical_cash_checkout_repository import PhysicalCashCheckoutRepository
from app.domains.pos.physical_cash_refund_repository import PhysicalCashRefundRepository
from app.domains.pos.physical_cash_refund_service import _normalize_reason
from app.domains.pos.physical_zero_return_service import _assert_reason_policy
from app.domains.pos.unit_electronic_refund_posting_schemas import ElectronicRefundPaymentV3


def _receipt_lines(
    original: MixedCheckoutResultV4,
    evidence: ReconciledMixedElectronicRefund,
    journal: MixedReturnJournalResult,
) -> tuple[MixedCashRefundLineV4, ...]:
    preview = evidence.activation.review.preview
    if (
        journal.operation_id,
        journal.parent_sale_id,
        journal.tenant_id,
        journal.branch_id,
        journal.actor_id,
        journal.total_amount,
    ) != (
        evidence.activation.operation_id,
        original.sale_id,
        original.tenant_id,
        original.branch_id,
        evidence.activation.actor_id,
        preview.total_amount,
    ):
        raise AurumError("Mixed electronic refund quarantine scope differs")
    if len(journal.lines) != len(preview.lines):
        raise AurumError("Mixed electronic refund quarantine lines differ")
    for line, quote in zip(journal.lines, preview.lines, strict=True):
        received = line.model_dump(mode="json")
        expected = quote.model_dump(mode="json")
        if any(received.get(key) != value for key, value in expected.items()):
            raise AurumError("Mixed electronic refund quarantine allocation differs")
    # Reuse only the pure historical line renderer; its cash document is not persisted.
    return mixed_cash_receipt(original, journal, 1, "internal-line-render").lines


class MixedElectronicRefundPostingService:
    def __init__(self, repository: MixedElectronicRefundPostingRepository) -> None:
        self.repository = repository
        session = repository.session
        self.outcomes = MixedElectronicRefundOutcomeService(
            MixedElectronicRefundOutcomeRepository(session)
        )
        self.bindings = MixedElectronicRefundBindingRepository(session)
        self.journal = MixedReturnJournalRepository(session)
        self.financial = PhysicalCashRefundRepository(session)
        self.revalidation = MixedElectronicRefundRevalidationService(
            MixedElectronicRefundIntentRepository(session)
        )

    async def post(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        parent_sale_id: UUID,
        operation_id: UUID,
        shift_id: UUID,
        reason_code: str | None = None,
        comment: str | None = None,
    ) -> PostedMixedElectronicRefund:
        session = self.repository.session
        if session.new or session.dirty or session.deleted:
            raise ConflictError("Persist pending changes before electronic refund posting")
        reason_code, comment = _normalize_reason(reason_code, comment)
        async with session.begin_nested():
            await self.bindings.lock_operation(tenant_id, operation_id)
            await self.journal.lock_operation(tenant_id, operation_id)
            evidence = await self.outcomes.read(
                tenant_id=tenant_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
                parent_sale_id=parent_sale_id,
                operation_id=operation_id,
            )
            parent = await self.journal.lock_sale(tenant_id, parent_sale_id)
            if parent is None:
                raise NotFoundError("Original electronic refund sale not found")
            marker = await self.repository.get(tenant_id, operation_id)
            if marker is not None:
                if (marker.shift_id, marker.reason_code, marker.comment) != (
                    shift_id,
                    reason_code,
                    comment,
                ):
                    raise ConflictError("Electronic refund was posted with other details")
                result = await self._saved(marker, evidence, tenant_id, actor_id)
                await self.journal.assert_actor(tenant_id, actor_id, marker.branch_id)
                return result
            if evidence.state != "confirmed_evidence_only":
                raise ConflictError("Electronic refund requires every terminal confirmation")
            saved = await self.revalidation.revalidate_locked_review(
                tenant_id=tenant_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
                parent_sale_id=parent_sale_id,
                operation_id=operation_id,
            )
            if saved != evidence.activation.review:
                raise ConflictError("Electronic refund review differs")
            await self._assert_new_post(tenant_id, actor_id, parent, shift_id, reason_code, comment)
            marker = MixedElectronicRefundPosting(
                tenant_id=tenant_id,
                id=operation_id,
                parent_sale_id=parent_sale_id,
                sale_id=uuid4(),
                branch_id=parent.branch_id,
                register_id=parent.register_id,
                shift_id=shift_id,
                actor_id=actor_id,
                reason_code=reason_code,
                comment=comment,
                created_by=actor_id,
            )
            await self.repository.insert(marker)
            received = await MixedReturnJournalService(self.journal).receive(
                tenant_id=tenant_id,
                actor_id=actor_id,
                operation_id=operation_id,
                parent_sale_id=parent_sale_id,
                selections=saved.request.selection.selections(),
            )
            checkout = await self.journal.get_checkout(tenant_id, parent_sale_id)
            if checkout is None:
                raise AurumError("Original electronic checkout is unavailable")
            original = MixedCheckoutResultV4.model_validate(checkout.result_payload)
            allocation = await PhysicalCashCheckoutRepository(session).allocate_receipt(
                parent.register_id
            )
            if allocation is None:
                raise AurumError("Electronic refund receipt allocation failed")
            sequence, number = allocation
            confirmations = {part.original_payment_id: part for part in evidence.confirmations}
            receipt = MixedElectronicRefundReceiptV4(
                parent_sale_id=parent_sale_id,
                parent_receipt_number=original.receipt_number,
                receipt_number=number,
                receipt_sequence=sequence,
                completed_at=utc_now(),
                lines=_receipt_lines(original, evidence, received),
                total_amount=received.total_amount,
                payments=tuple(
                    ElectronicRefundPaymentV3(
                        id=uuid4(),
                        original_payment_id=part.original_payment_id,
                        method=part.method,
                        amount=part.amount,
                        terminal_id=(
                            confirmations[part.original_payment_id].terminal_id
                            if part.method != "cash"
                            else None
                        ),
                        document_number=(
                            confirmations[part.original_payment_id].document_number
                            if part.method != "cash"
                            else None
                        ),
                    )
                    for part in sorted(
                        saved.preview.payments, key=lambda p: str(p.original_payment_id)
                    )
                    if part.amount > 0
                ),
            )
            sale = Sale(
                id=marker.sale_id,
                tenant_id=tenant_id,
                branch_id=parent.branch_id,
                register_id=parent.register_id,
                shift_id=shift_id,
                cashier_user_id=actor_id,
                sale_type="return",
                parent_sale_id=parent_sale_id,
                status="draft",
                is_test=False,
                total_amount=received.total_amount,
                currency="TJS",
                operation_id=operation_id,
                operation_hash=saved.request.expected_revision_hash,
            )
            await self.financial.create_sale(sale)
            for part in receipt.payments:
                await self.repository.record_payment(marker, part)
            sale.status, sale.completed_at = "completed", receipt.completed_at
            sale.receipt_number, sale.receipt_seq = receipt.receipt_number, receipt.receipt_sequence
            sale.receipt_snapshot = receipt.model_dump(mode="json")
            await session.flush()
            await self.repository.validate_completion()
            await self.journal.assert_actor(tenant_id, actor_id, parent.branch_id)
            return await self._saved(marker, evidence, tenant_id, actor_id)

    async def _assert_new_post(
        self,
        tenant_id: UUID,
        actor_id: UUID,
        parent: Sale,
        shift_id: UUID,
        reason_code: str | None,
        comment: str | None,
    ) -> None:
        shift = await self.financial.lock_shift(tenant_id, shift_id)
        if shift is None or (
            shift.status,
            shift.closed_at,
            shift.branch_id,
            shift.register_id,
            shift.opened_by_user_id,
            shift.currency,
        ) != ("open", None, parent.branch_id, parent.register_id, actor_id, "TJS"):
            raise BusinessRuleError("Refund requires your open original-register shift")
        if not await self.financial.lock_active_register(
            tenant_id, parent.branch_id, parent.register_id
        ):
            raise BusinessRuleError("Refund requires an active original register")
        settings = await self.financial.get_settings_for_pos(tenant_id)
        if settings is None:
            raise AurumError("POS settings are unavailable")
        _assert_reason_policy(settings.refund_reason_mode, reason_code, comment)
        await self.journal.assert_actor(tenant_id, actor_id, parent.branch_id)

    async def read(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        parent_sale_id: UUID,
        operation_id: UUID,
    ) -> PostedMixedElectronicRefund:
        with self.repository.session.no_autoflush:
            evidence = await self.outcomes.read(
                tenant_id=tenant_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
                parent_sale_id=parent_sale_id,
                operation_id=operation_id,
            )
            marker = await self.repository.get(tenant_id, operation_id)
            if marker is None:
                raise NotFoundError("Posted electronic refund not found")
            result = await self._saved(marker, evidence, tenant_id, actor_id)
            await self.journal.assert_actor(tenant_id, actor_id, marker.branch_id)
            return result

    async def _saved(
        self,
        marker: MixedElectronicRefundPosting,
        evidence: ReconciledMixedElectronicRefund,
        tenant_id: UUID,
        actor_id: UUID,
    ) -> PostedMixedElectronicRefund:
        try:
            activation = evidence.activation
            sale = await self.repository.sale(tenant_id, marker.sale_id)
            journal = await self.journal.get_command(tenant_id, marker.id)
            checkout = await self.journal.get_checkout(tenant_id, marker.parent_sale_id)
            if sale is None or journal is None or checkout is None:
                raise ValueError("Posting facts missing")
            if evidence.state != "confirmed_evidence_only" or (
                marker.tenant_id,
                marker.id,
                marker.parent_sale_id,
                marker.branch_id,
                marker.actor_id,
                marker.created_by,
                marker.register_id,
            ) != (
                tenant_id,
                activation.operation_id,
                activation.parent_sale_id,
                activation.branch_id,
                actor_id,
                actor_id,
                activation.register_id,
            ):
                raise ValueError("Posting scope differs")
            receipt = MixedElectronicRefundReceiptV4.model_validate(sale.receipt_snapshot)
            received = MixedReturnJournalResult.model_validate(journal.result_payload)
            original = MixedCheckoutResultV4.model_validate(checkout.result_payload)
            if (
                receipt.lines != _receipt_lines(original, evidence, received)
                or (receipt.parent_receipt_number != original.receipt_number)
                or (
                    sale.tenant_id,
                    sale.parent_sale_id,
                    sale.branch_id,
                    sale.register_id,
                    sale.shift_id,
                    sale.cashier_user_id,
                    sale.status,
                    sale.sale_type,
                    sale.is_test,
                    sale.currency,
                    sale.operation_id,
                    sale.operation_hash,
                    sale.total_amount,
                    sale.completed_at,
                    sale.receipt_number,
                    sale.receipt_seq,
                )
                != (
                    tenant_id,
                    marker.parent_sale_id,
                    marker.branch_id,
                    marker.register_id,
                    marker.shift_id,
                    actor_id,
                    "completed",
                    "return",
                    False,
                    "TJS",
                    marker.id,
                    activation.review.request.expected_revision_hash,
                    receipt.total_amount,
                    receipt.completed_at,
                    receipt.receipt_number,
                    receipt.receipt_sequence,
                )
            ):
                raise ValueError("Posting receipt differs")
            await self._validate_payments(marker, evidence, receipt)
            return PostedMixedElectronicRefund(
                operation_id=marker.id,
                parent_sale_id=marker.parent_sale_id,
                sale_id=marker.sale_id,
                tenant_id=tenant_id,
                branch_id=marker.branch_id,
                register_id=marker.register_id,
                shift_id=marker.shift_id,
                actor_id=actor_id,
                total_amount=receipt.total_amount,
                physical_cost_amount=received.physical_cost_amount,
                receipt=receipt,
            )
        except (ValidationError, ValueError, TypeError, KeyError):
            raise AurumError("Stored electronic refund posting is inconsistent") from None

    async def _validate_payments(
        self,
        marker: MixedElectronicRefundPosting,
        evidence: ReconciledMixedElectronicRefund,
        receipt: MixedElectronicRefundReceiptV4,
    ) -> None:
        expected = {
            p.original_payment_id: p
            for p in evidence.activation.review.preview.payments
            if p.amount > 0
        }
        confirmed = {p.original_payment_id: p for p in evidence.confirmations}
        payouts = {
            p.id: p for p in await self.repository.payments(marker.tenant_id, marker.sale_id)
        }
        components = {
            p.id: p for p in await self.repository.components(marker.tenant_id, marker.sale_id)
        }
        if (
            set(payouts) != {p.id for p in receipt.payments}
            or set(components) != set(payouts)
            or ({p.original_payment_id for p in receipt.payments} != set(expected))
        ):
            raise AurumError("Stored electronic refund payments are incomplete")
        for part in receipt.payments:
            source, payout, component = (
                expected[part.original_payment_id],
                payouts[part.id],
                components[part.id],
            )
            metadata = (
                None
                if part.method == "cash"
                else {"terminal_id": part.terminal_id, "document_number": part.document_number}
            )
            if (
                (part.method, part.amount, part.currency)
                != (source.method, source.amount, source.currency)
                or (
                    payout.payment_method,
                    payout.amount,
                    payout.currency,
                    payout.payment_attempt_id,
                    payout.metadata_json,
                    payout.operation_id,
                    payout.operation_hash,
                )
                != (part.method, part.amount, "TJS", None, metadata, None, None)
                or (
                    component.parent_sale_id,
                    component.original_payment_id,
                    component.physical_refund_id,
                    component.mixed_refund_id,
                    component.electronic_refund_id,
                    component.mixed_electronic_refund_id,
                    component.method,
                    component.amount,
                    component.currency,
                    component.created_by,
                )
                != (
                    marker.parent_sale_id,
                    part.original_payment_id,
                    None,
                    None,
                    None,
                    marker.id,
                    part.method,
                    part.amount,
                    "TJS",
                    marker.actor_id,
                )
            ):
                raise AurumError("Stored electronic refund payment allocation differs")
            if part.method != "cash" and (part.terminal_id, part.document_number) != (
                confirmed[part.original_payment_id].terminal_id,
                confirmed[part.original_payment_id].document_number,
            ):
                raise AurumError("Stored electronic refund terminal evidence differs")
