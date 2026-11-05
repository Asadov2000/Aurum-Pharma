"""Atomic saved training checkout; virtual stock and simulated payments only."""

import json
from datetime import date
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.core.errors import (
    AurumError,
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.time import utc_now
from app.domains.foundation.repository import FoundationRepository
from app.domains.pos.models import Sale
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.domains.pos.training_checkout_repository import (
    TrainingCheckoutRepository,
    TrainingSaleState,
    TrainingSavedState,
)
from app.domains.pos.training_checkout_schemas import (
    TrainingCheckoutOperationCompleted,
    TrainingCheckoutOperationNotFound,
    TrainingCheckoutOperationRead,
    TrainingCheckoutRequest,
    TrainingCheckoutResultV1,
    TrainingReceiptDocumentV1,
    _change,
    training_checkout_request_hash,
    training_receipt_lines_from_quote,
)
from app.domains.pos.training_simulation import simulate_training_sale
from app.domains.pos.unit_cart_quote import MixedCartQuoteV2, UnitCartQuoteService
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_contracts import LegacyResultLineV2
from app.domains.pos.unit_prescription_repository import UnitPrescriptionRepository
from app.domains.pos.unit_prescription_service import UnitPrescriptionService


def _identifiers(
    tenant_id: UUID,
    actor_id: UUID,
    sale_id: UUID,
    operation_id: UUID,
    branches: set[UUID] | None,
) -> None:
    if not all(isinstance(value, UUID) for value in (tenant_id, actor_id, sale_id, operation_id)):
        raise BusinessRuleError("Training checkout identifiers must be UUIDs")
    if operation_id.version != 4:
        raise BusinessRuleError("Training checkout operation must be UUID4")
    if branches is not None and (
        not isinstance(branches, set) or any(not isinstance(branch, UUID) for branch in branches)
    ):
        raise PermissionDeniedError("Explicit training branch access is required")


class TrainingCheckoutService:
    def __init__(self, repository: TrainingCheckoutRepository) -> None:
        self.repository = repository
        self.cart = UnitCartRepository(repository.session)

    async def _stock_date(self, tenant_id: UUID) -> date:
        settings = await FoundationRepository(self.repository.session).get_settings_for_pos(
            tenant_id
        )
        timezone_name = settings.report_timezone if settings is not None else "Asia/Dushanbe"
        try:
            return utc_now().astimezone(ZoneInfo(timezone_name)).date()
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise AurumError("Tenant report timezone is invalid") from error

    async def _authorize(
        self, sale: Sale | TrainingSaleState, actor_id: UUID, branches: set[UUID] | None
    ) -> set[UUID]:
        allowed = {sale.branch_id} if branches is None else branches
        if sale.branch_id not in allowed or sale.cashier_user_id != actor_id:
            raise PermissionDeniedError("Training checkout branch or cashier access denied")
        await self.cart.assert_actor(sale.tenant_id, actor_id, sale.branch_id)
        return allowed

    @staticmethod
    def _result(saved: TrainingSavedState, sale: TrainingSaleState) -> TrainingCheckoutResultV1:
        try:
            # Persisted JSON needs JSON-mode date validation; Python mode accepts date objects only.
            result = TrainingCheckoutResultV1.model_validate_json(
                json.dumps(saved.result_payload, ensure_ascii=False, allow_nan=False)
            )
            receipt = TrainingReceiptDocumentV1.model_validate_json(
                json.dumps(saved.receipt_payload, ensure_ascii=False, allow_nan=False)
            )
            source = TrainingReceiptDocumentV1.model_validate_json(
                json.dumps(sale.receipt_snapshot, ensure_ascii=False, allow_nan=False)
            )
        except ValidationError as error:
            raise AurumError("Saved training checkout is inconsistent") from error
        if (
            saved.id != result.operation_id
            or saved.sale_id != result.sale_id
            or saved.tenant_id != result.tenant_id
            or saved.branch_id != result.branch_id
            or saved.register_id != result.register_id
            or saved.shift_id != result.shift_id
            or saved.cashier_user_id != result.cashier_id
            or saved.created_by != result.cashier_id
            or saved.quote_revision != result.request.expected_revision_hash
            or saved.request_hash != training_checkout_request_hash(result.request)
            or saved.total_amount != result.total_amount
            or saved.receipt_number != result.receipt_number
            or saved.receipt_seq != result.receipt_sequence
            or receipt != result.receipt
            or source != result.receipt
            or sale.id != result.sale_id
            or sale.tenant_id != result.tenant_id
            or sale.branch_id != result.branch_id
            or sale.register_id != result.register_id
            or sale.shift_id != result.shift_id
            or sale.cashier_user_id != result.cashier_id
            or sale.status != "completed"
            or sale.sale_type != "sale"
            or sale.is_test is not True
            or sale.currency != result.currency
            or sale.total_amount != result.total_amount
            or sale.receipt_number != result.receipt_number
            or sale.receipt_seq != result.receipt_sequence
            or sale.completed_at != result.completed_at
        ):
            raise AurumError("Saved training checkout differs from its sale or journal")
        return result

    async def read(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        sale_id: UUID,
        operation_id: UUID,
    ) -> TrainingCheckoutOperationRead:
        _identifiers(tenant_id, actor_id, sale_id, operation_id, allowed_branch_ids)
        with self.repository.session.no_autoflush:
            await self.cart.assert_actor(tenant_id, actor_id)
            snapshot = await self.repository.read(tenant_id, sale_id, operation_id)
            if snapshot is None:
                raise NotFoundError("Training sale not found")
            sale, saved = snapshot
            await self._authorize(sale, actor_id, allowed_branch_ids)
            if sale.is_test is not True:
                raise BusinessRuleError("Training recovery requires a saved test sale")
            if saved is None:
                return TrainingCheckoutOperationNotFound(operation_id=operation_id, sale_id=sale_id)
            result = self._result(saved, sale)
            await self.cart.assert_actor(tenant_id, actor_id, sale.branch_id)
            return TrainingCheckoutOperationCompleted(
                operation_id=operation_id, sale_id=sale_id, result=result
            )

    async def complete(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        request: TrainingCheckoutRequest,
    ) -> TrainingCheckoutResultV1:
        if not isinstance(request, TrainingCheckoutRequest):
            raise BusinessRuleError("A typed training checkout request is required")
        request = TrainingCheckoutRequest.model_validate(request.model_dump())
        _identifiers(tenant_id, actor_id, request.sale_id, request.operation_id, allowed_branch_ids)
        session = self.repository.session
        if session.new or session.dirty or session.deleted:
            raise ConflictError("Persist pending changes before training checkout")
        try:
            async with session.begin_nested():
                await self.cart.assert_actor(tenant_id, actor_id)
                sale = await self.cart.lock_sale(tenant_id, request.sale_id)
                if sale is None:
                    raise NotFoundError("Training sale not found")
                branches = await self._authorize(sale, actor_id, allowed_branch_ids)
                if sale.is_test is not True:
                    raise BusinessRuleError("Training checkout requires a saved test sale")
                saved = await self.repository.get(tenant_id, request.operation_id)
                if saved is not None:
                    if saved.request_hash != training_checkout_request_hash(request):
                        raise ConflictError("Training operation was reused for a different request")
                    result = self._result(saved, TrainingSaleState.model_validate(sale))
                    await self.cart.assert_actor(tenant_id, actor_id, sale.branch_id)
                    return result
                return await self._complete(sale, actor_id, branches, request)
        except IntegrityError as error:
            if getattr(error.orig, "sqlstate", None) == "23505":
                raise ConflictError("Training operation or sale was already completed") from error
            raise

    async def _require_prescriptions(
        self, sale: Sale, quote: MixedCartQuoteV2, actor_id: UUID, branches: set[UUID]
    ) -> None:
        required: set[UUID] = set()
        for catalog_id in sorted({line.catalog_id for line in quote.lines}, key=str):
            catalog = await self.cart.lock_catalog(sale.tenant_id, catalog_id)
            if catalog is None or not catalog.is_active or catalog.deleted_at is not None:
                raise BusinessRuleError("Training checkout contains an unavailable medicine")
            if catalog.currency != "TJS" or catalog.dispensing_type == "special":
                raise BusinessRuleError("Training checkout contains an unsupported medicine")
            if catalog.dispensing_type == "prescription":
                required.update(
                    line.id
                    for line in quote.lines
                    if isinstance(line, LegacyResultLineV2) and line.catalog_id == catalog_id
                )
        prescriptions = UnitPrescriptionRepository(self.repository.session)
        await UnitPrescriptionService(prescriptions).require_complete(
            tenant_id=sale.tenant_id,
            sale_id=sale.id,
            actor_id=actor_id,
            allowed_branch_ids=branches,
        )
        if required:
            references = await prescriptions.references(sale.tenant_id, sale.id)
            if not any(legacy is None and physical is None for legacy, physical in references):
                covered = {legacy for legacy, _ in references if legacy is not None}
                if required - covered:
                    raise BusinessRuleError(
                        "Prescription details are required for all legacy Rx positions"
                    )

    async def _complete(
        self, sale: Sale, actor_id: UUID, branches: set[UUID], request: TrainingCheckoutRequest
    ) -> TrainingCheckoutResultV1:
        quote = await UnitCartQuoteService(self.cart).quote(
            tenant_id=sale.tenant_id,
            sale_id=sale.id,
            actor_id=actor_id,
            allowed_branch_ids=branches,
        )
        if (
            quote.revision_hash != request.expected_revision_hash
            or quote.total_amount != request.expected_total_amount
        ):
            raise ConflictError("Training cart changed; refresh its quote before completion")
        await self._require_prescriptions(sale, quote, actor_id, branches)
        await POSService(
            POSRepository(self.repository.session)
        )._validate_pos_payment_configuration(
            tenant_id=sale.tenant_id,
            requested_methods={part.method for part in request.simulated_payments},
        )
        today = await self._stock_date(sale.tenant_id)
        physical, legacy = await self.repository.stock_snapshots(quote, today)
        try:
            simulation = simulate_training_sale(
                quote=quote, physical_batches=physical, legacy_batches=legacy, today=today
            )
        except ValueError as error:
            raise BusinessRuleError(
                "Training stock simulation cannot fulfil the current cart"
            ) from error
        names = await self.repository.names(
            sale.tenant_id, {line.catalog_id for line in quote.lines}
        )
        allocated = await self.repository.allocate_receipt(sale.register_id)
        if allocated is None:
            raise AurumError("Training receipt number could not be allocated")
        sequence, number = allocated
        headers = {
            "operation_id": request.operation_id,
            "sale_id": sale.id,
            "tenant_id": sale.tenant_id,
            "branch_id": sale.branch_id,
            "register_id": sale.register_id,
            "shift_id": sale.shift_id,
            "cashier_id": actor_id,
            "receipt_number": f"TEST-{number}",
            "receipt_sequence": sequence,
            "completed_at": utc_now(),
            "total_amount": quote.total_amount,
        }
        receipt = TrainingReceiptDocumentV1.model_validate(
            {
                **headers,
                "lines": training_receipt_lines_from_quote(quote, names),
                "simulated_payments": request.simulated_payments,
                "cash_tendered": request.cash_tendered,
                "change_amount": _change(
                    request.simulated_payments, quote.total_amount, request.cash_tendered
                ),
            }
        )
        result = TrainingCheckoutResultV1.model_validate(
            {**headers, "request": request, "receipt": receipt, "simulation": simulation}
        )
        await self.cart.assert_actor(sale.tenant_id, actor_id, sale.branch_id)
        await self.repository.insert(result, training_checkout_request_hash(request))
        await self.repository.complete_sale(sale, result)
        await self.repository.validate_completion()
        await self.cart.assert_actor(sale.tenant_id, actor_id, sale.branch_id)
        return result
