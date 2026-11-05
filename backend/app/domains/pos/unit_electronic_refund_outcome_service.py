"""Recover terminal confirmations without sending money or posting a return."""

from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.core.errors import AurumError, BusinessRuleError, ConflictError, NotFoundError
from app.domains.pos.physical_return_journal_repository import PhysicalReturnJournalRepository
from app.domains.pos.unit_electronic_refund_activation_repository import (
    UnitElectronicRefundActivationRepository,
)
from app.domains.pos.unit_electronic_refund_activation_schemas import (
    SavedElectronicRefundActivation,
)
from app.domains.pos.unit_electronic_refund_activation_service import (
    UnitElectronicRefundActivationService,
)
from app.domains.pos.unit_electronic_refund_binding_repository import (
    UnitElectronicRefundBindingRepository,
)
from app.domains.pos.unit_electronic_refund_outcome_models import UnitElectronicRefundOutcome
from app.domains.pos.unit_electronic_refund_outcome_repository import (
    UnitElectronicRefundOutcomeRepository,
)
from app.domains.pos.unit_electronic_refund_outcome_schemas import (
    ElectronicRefundConfirmation,
    ReconciledElectronicRefund,
    SavedElectronicRefundConfirmation,
)


class UnitElectronicRefundOutcomeService:
    def __init__(self, repository: UnitElectronicRefundOutcomeRepository) -> None:
        self.repository = repository
        self.activations = UnitElectronicRefundActivationService(
            UnitElectronicRefundActivationRepository(repository.session)
        )
        self.bindings = UnitElectronicRefundBindingRepository(repository.session)
        self.authority = PhysicalReturnJournalRepository(repository.session)

    async def record(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        parent_sale_id: UUID,
        operation_id: UUID,
        confirmation: ElectronicRefundConfirmation,
    ) -> ReconciledElectronicRefund:
        """Record operator-verified evidence; never invoke a terminal on a retry.

        The caller owns commit. A new record needs writable tenant access, but
        a previously committed activation remains reconcilable after shift
        closure. Exact replay recovers the stored facts, including in readonly.
        """
        session = self.repository.session
        if session.new or session.dirty or session.deleted:
            raise ConflictError("Persist pending changes before recording refund evidence")
        async with session.begin_nested():
            await self.bindings.lock_operation(tenant_id, operation_id)
            activation = await self.activations.read(
                tenant_id=tenant_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
                parent_sale_id=parent_sale_id,
                operation_id=operation_id,
            )
            parent = await self.authority.lock_sale(tenant_id, parent_sale_id)
            if parent is None:
                raise NotFoundError("Original electronic refund sale not found")
            if (parent.tenant_id, parent.id, parent.branch_id, parent.register_id) != (
                tenant_id,
                parent_sale_id,
                activation.branch_id,
                activation.register_id,
            ):
                raise AurumError("Stored electronic refund source is inconsistent")
            rows = await self.repository.list(tenant_id, operation_id)
            result = self._result(activation, rows, tenant_id, actor_id)
            previous = next(
                (
                    row
                    for row in rows
                    if row.original_payment_id == confirmation.original_payment_id
                ),
                None,
            )
            if previous is not None:
                if (previous.terminal_id, previous.document_number) != (
                    confirmation.terminal_id,
                    confirmation.document_number,
                ):
                    raise ConflictError("Refund payment was confirmed with other terminal evidence")
            else:
                payment = next(
                    (
                        part
                        for part in activation.review.preview.payments
                        if part.original_payment_id == confirmation.original_payment_id
                        and part.method in {"card", "qr"}
                        and part.amount > 0
                    ),
                    None,
                )
                if payment is None:
                    raise BusinessRuleError("Evidence must match an original electronic allocation")
                await self.authority.assert_actor(tenant_id, actor_id, activation.branch_id)
                row = UnitElectronicRefundOutcome(
                    tenant_id=tenant_id,
                    id=uuid4(),
                    operation_id=operation_id,
                    parent_sale_id=parent_sale_id,
                    branch_id=activation.branch_id,
                    actor_id=actor_id,
                    original_payment_id=payment.original_payment_id,
                    method=payment.method,
                    amount=payment.amount,
                    currency=payment.currency,
                    terminal_id=confirmation.terminal_id,
                    document_number=confirmation.document_number,
                    created_by=actor_id,
                )
                try:
                    await self.repository.insert(row)
                except IntegrityError as exc:
                    raise ConflictError("Terminal evidence was already used") from exc
                result = self._result(activation, (*rows, row), tenant_id, actor_id)
            await self.authority.assert_actor(tenant_id, actor_id, activation.branch_id)
            return result

    async def read(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        parent_sale_id: UUID,
        operation_id: UUID,
    ) -> ReconciledElectronicRefund:
        with self.repository.session.no_autoflush:
            activation = await self.activations.read(
                tenant_id=tenant_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
                parent_sale_id=parent_sale_id,
                operation_id=operation_id,
            )
            rows = await self.repository.list(tenant_id, operation_id)
            await self.authority.assert_actor(tenant_id, actor_id, activation.branch_id)
            return self._result(activation, rows, tenant_id, actor_id)

    @staticmethod
    def _result(
        activation: SavedElectronicRefundActivation,
        rows: tuple[UnitElectronicRefundOutcome, ...],
        tenant_id: UUID,
        actor_id: UUID,
    ) -> ReconciledElectronicRefund:
        try:
            payments = {
                part.original_payment_id: part
                for part in activation.review.preview.payments
                if part.method in {"card", "qr"} and part.amount > 0
            }
            if not payments:
                raise ValueError("No electronic allocation")
            seen: set[UUID] = set()
            evidence: set[tuple[str, str]] = set()
            confirmations: list[SavedElectronicRefundConfirmation] = []
            for row in sorted(rows, key=lambda value: str(value.original_payment_id)):
                part = payments.get(row.original_payment_id)
                if part is None or row.original_payment_id in seen:
                    raise ValueError("Unknown or duplicate payment")
                if (row.terminal_id, row.document_number) in evidence:
                    raise ValueError("Duplicate terminal evidence")
                if (
                    row.tenant_id,
                    row.operation_id,
                    row.parent_sale_id,
                    row.branch_id,
                    row.actor_id,
                    row.created_by,
                    row.method,
                    row.amount,
                    row.currency,
                ) != (
                    tenant_id,
                    activation.operation_id,
                    activation.parent_sale_id,
                    activation.branch_id,
                    actor_id,
                    actor_id,
                    part.method,
                    part.amount,
                    "TJS",
                ) or row.created_at < activation.activated_at:
                    raise ValueError("Stored evidence scope differs")
                saved = SavedElectronicRefundConfirmation(
                    original_payment_id=row.original_payment_id,
                    method=part.method,
                    amount=row.amount,
                    currency="TJS",
                    terminal_id=row.terminal_id,
                    document_number=row.document_number,
                    recorded_at=row.created_at,
                )
                if (saved.terminal_id, saved.document_number) != (
                    row.terminal_id,
                    row.document_number,
                ):
                    raise ValueError("Stored evidence is not canonical")
                confirmations.append(saved)
                seen.add(row.original_payment_id)
                evidence.add((row.terminal_id, row.document_number))
            missing = tuple(sorted(payments.keys() - seen, key=str))
            return ReconciledElectronicRefund(
                state="requires_reconciliation" if missing else "confirmed_evidence_only",
                activation=activation,
                confirmations=tuple(confirmations),
                missing_payment_ids=missing,
            )
        except (ValidationError, ValueError, TypeError):
            raise AurumError("Stored electronic refund evidence is inconsistent") from None
