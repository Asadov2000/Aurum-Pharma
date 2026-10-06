"""Transient combined checkout checks, never a payment, reservation or committed sale."""

from dataclasses import dataclass
from datetime import date
from typing import Literal
from uuid import UUID

from sqlalchemy.exc import DBAPIError

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.domains.inventory.unit_stock import UnitStockBatch, UnitStockShortage
from app.domains.pos.unit_cart_quote import UnitDraftQuoteLineV2
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_cart_service import UnitCartService
from app.domains.pos.unit_cart_stock import UnitCartStockLine, plan_unit_cart_stock
from app.domains.pos.unit_cash_confirmation import (
    CashConfirmationRequestV2,
    UnitCashConfirmationService,
)
from app.domains.pos.unit_cash_intent_repository import UnitCashIntentRepository
from app.domains.pos.unit_cash_intent_service import SavedCashIntentV2, UnitCashIntentService
from app.domains.pos.unit_checkout_repository import UnitCheckoutRepository
from app.domains.pos.unit_prescription_repository import UnitPrescriptionRepository
from app.domains.pos.unit_prescription_service import UnitPrescriptionService


@dataclass(frozen=True)
class UnitCheckoutPreparation:
    """Only valid while its caller transaction holds the locks; cannot be posted."""

    confirmation: SavedCashIntentV2
    stock_plans: tuple[UnitCartStockLine, ...]
    stock_date: date
    state: Literal["checked_draft"] = "checked_draft"


class UnitCheckoutPreparationService:
    def __init__(self, repository: UnitCheckoutRepository) -> None:
        self.repository = repository
        self.cart = UnitCartRepository(repository.session)

    async def prepare(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
        request: CashConfirmationRequestV2,
        today: date,
    ) -> UnitCheckoutPreparation:
        """Recheck saved cash, current retail/Rx and all unit demand in one transaction.

        `today` is the pharmacy's date supplied by the trusted caller. Allocation
        includes virtual opened packs and must never be used as posting input.
        Actual checkout must repeat these checks and atomically write all facts.
        Catalog NOWAIT contention requires retrying in a fresh caller transaction,
        which releases any SHARE locks inherited from an earlier quote.
        """
        request = CashConfirmationRequestV2.model_validate(request)
        if type(today) is not date:
            raise BusinessRuleError("Stock date must be a calendar date")
        try:
            return await self._prepare(
                tenant_id=tenant_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
                request=request,
                today=today,
            )
        except DBAPIError as error:
            # The helper's savepoint has already rolled back; the caller's
            # transaction remains usable, but must end before a busy retry.
            if getattr(error.orig, "sqlstate", None) == "55P03":
                raise ConflictError("Checkout stock is busy; retry in a new transaction") from error
            raise

    async def _prepare(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
        request: CashConfirmationRequestV2,
        today: date,
    ) -> UnitCheckoutPreparation:
        async with self.repository.session.begin_nested():
            sale = await UnitCartService(self.cart).lock_editable_sale(
                tenant_id,
                request.sale_id,
                actor_id,
                allowed_branch_ids,
            )
            units = await self.cart.list_items(tenant_id, sale.id)
            if not units:
                raise BusinessRuleError("Unit checkout preparation requires unit cart positions")
            if any(item.physical_packaging is not None for item in units):
                raise BusinessRuleError(
                    "Physical unit cart requires physical checkout; legacy stock cannot be used"
                )
            legacy = await self.cart.list_legacy_items(tenant_id, sale.id)
            legacy_catalog_ids = {item.catalog_id for item in legacy}
            legacy_rx_required = False
            # Acquire write-compatible catalog locks before quote/Rx SHARE locks.
            # Two different drafts must never try to upgrade shared catalog locks.
            for catalog_id in sorted(
                {item.catalog_id for item in units} | legacy_catalog_ids, key=str
            ):
                catalog = await self.repository.lock_checkout_catalog(tenant_id, catalog_id)
                if catalog is None:
                    raise NotFoundError("Checkout catalog item not found")
                if (
                    not catalog.is_active
                    or catalog.deleted_at is not None
                    or catalog.currency != "TJS"
                ):
                    raise BusinessRuleError("Checkout contains an unavailable medicine")
                if catalog.dispensing_type == "special":
                    raise BusinessRuleError("Special-dispensing medicines are unavailable")
                if catalog.dispensing_type == "prescription" and catalog_id in legacy_catalog_ids:
                    legacy_rx_required = True
            if legacy_rx_required and not await UnitPrescriptionRepository(
                self.repository.session
            ).references(tenant_id, sale.id):
                raise BusinessRuleError("Prescription details are required for legacy Rx positions")
            saved = await UnitCashIntentService(
                UnitCashIntentRepository(self.repository.session)
            ).read_saved(
                tenant_id=tenant_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
                request=request,
            )
            current = await UnitCashConfirmationService(self.cart).validate(
                tenant_id=tenant_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
                request=request,
            )
            if current != saved.confirmation:
                raise ConflictError("Saved cash confirmation differs from the current cart")
            await UnitPrescriptionService(
                UnitPrescriptionRepository(self.repository.session)
            ).require_complete(
                tenant_id=tenant_id,
                sale_id=sale.id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
            )
            lines = tuple(
                line for line in current.quote.lines if isinstance(line, UnitDraftQuoteLineV2)
            )
            batches: list[UnitStockBatch] = []
            for catalog_id in sorted({line.catalog_id for line in lines}, key=str):
                batches.extend(
                    await self.repository.lock_received_batches(
                        tenant_id, sale.branch_id, catalog_id
                    )
                )
            try:
                plans = plan_unit_cart_stock(
                    lines=lines,
                    batches=tuple(batches),
                    tenant_id=tenant_id,
                    branch_id=sale.branch_id,
                    today=today,
                )
            except UnitStockShortage as error:
                raise BusinessRuleError(
                    "Insufficient received stock for the entire unit cart",
                    details={
                        "requested_base_units": error.requested_base_units,
                        "available_base_units": error.available_base_units,
                    },
                ) from error
            return UnitCheckoutPreparation(confirmation=saved, stock_plans=plans, stock_date=today)
