"""Transient checks for legacy and physical stock; never authorize financial posting."""

from dataclasses import dataclass
from datetime import date
from typing import Literal
from uuid import UUID

from sqlalchemy.exc import DBAPIError

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.domains.inventory.repository import InventoryRepository
from app.domains.inventory.unit_allocation_v2 import (
    UnitCartLineV2,
    UnitCartStockPlanV2,
    plan_unit_cart_stock_v2,
)
from app.domains.pos.legacy_checkout_stock import (
    LegacyCheckoutAllocation,
    plan_legacy_checkout_allocations,
)
from app.domains.pos.models import Sale, SaleItem
from app.domains.pos.unit_cart_quote import PhysicalUnitDraftQuoteLineV2
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_cart_service import UnitCartService
from app.domains.pos.unit_cash_confirmation import (
    CashConfirmationRequestV2,
    UnitCashConfirmationService,
)
from app.domains.pos.unit_cash_intent_repository import UnitCashIntentRepository
from app.domains.pos.unit_cash_intent_service import SavedCashIntentV2, UnitCashIntentService
from app.domains.pos.unit_checkout_preparation import UnitCheckoutPreparationService
from app.domains.pos.unit_checkout_repository import UnitCheckoutRepository
from app.domains.pos.unit_prescription_repository import UnitPrescriptionRepository
from app.domains.pos.unit_prescription_service import UnitPrescriptionService

__all__ = [
    "LegacyCheckoutAllocation",
    "MixedPhysicalCheckoutPreparation",
    "MixedPhysicalCheckoutPreparationService",
]


@dataclass(frozen=True)
class MixedPhysicalCheckoutPreparation:
    """Checks expire with caller locks; this result cannot be used to post a sale."""

    confirmation: SavedCashIntentV2
    legacy_allocations: tuple[LegacyCheckoutAllocation, ...]
    physical_stock_plan: UnitCartStockPlanV2
    stock_date: date
    state: Literal["checked_mixed_physical_draft"] = "checked_mixed_physical_draft"


class MixedPhysicalCheckoutPreparationService:
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
    ) -> MixedPhysicalCheckoutPreparation:
        """Check both stock systems in one caller transaction without reserving either."""
        request = CashConfirmationRequestV2.model_validate(request)
        # begin_nested flushes pending ORM state before creating its savepoint.
        # The caller must persist its draft explicitly; this check never does so.
        session = self.repository.session
        if session.new or session.dirty or session.deleted:
            raise ConflictError("Persist pending draft changes before mixed checkout preparation")
        try:
            async with self.repository.session.begin_nested():
                return await self._prepare(tenant_id, actor_id, allowed_branch_ids, request)
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) == "55P03":
                raise ConflictError("Checkout stock is busy; retry in a new transaction") from error
            raise

    async def _prepare(
        self,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
        request: CashConfirmationRequestV2,
    ) -> MixedPhysicalCheckoutPreparation:
        cart_service = UnitCartService(self.cart)
        sale = await cart_service.lock_editable_sale(
            tenant_id, request.sale_id, actor_id, allowed_branch_ids
        )
        legacy = await self.cart.list_legacy_items(tenant_id, sale.id)
        units = await self.cart.list_items(tenant_id, sale.id)
        if not legacy or not units or any(item.physical_packaging is None for item in units):
            raise BusinessRuleError("Mixed checkout requires legacy and physical positions only")
        legacy_rx: set[UUID] = set()
        # Write-compatible locks precede all quote/Rx SHARE locks, as in physical checkout.
        catalog_ids = {item.catalog_id for item in legacy} | {item.catalog_id for item in units}
        for catalog_id in sorted(catalog_ids, key=str):
            catalog = await self.repository.lock_checkout_catalog(tenant_id, catalog_id)
            if catalog is None:
                raise NotFoundError("Checkout catalog item not found")
            if (
                not catalog.is_active
                or catalog.deleted_at is not None
                or catalog.currency != "TJS"
                or catalog.dispensing_type == "special"
            ):
                raise BusinessRuleError("Checkout contains an unavailable medicine")
            if catalog.dispensing_type == "prescription":
                legacy_rx.update(item.id for item in legacy if item.catalog_id == catalog_id)
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
        prescriptions = UnitPrescriptionRepository(self.repository.session)
        await UnitPrescriptionService(prescriptions).require_complete(
            tenant_id=tenant_id,
            sale_id=sale.id,
            actor_id=actor_id,
            allowed_branch_ids=allowed_branch_ids,
        )
        if legacy_rx:
            references = await prescriptions.references(tenant_id, sale.id)
            global_reference = any(a is None and b is None for a, b in references)
            covered = {item_id for item_id, _ in references if item_id is not None}
            if not global_reference and legacy_rx - covered:
                raise BusinessRuleError(
                    "Prescription details are required for all legacy Rx positions"
                )
        today = await UnitCheckoutPreparationService(self.repository)._physical_stock_date(
            tenant_id
        )
        allocations = await self._legacy_allocations(sale, legacy, today)
        physical = tuple(
            UnitCartLineV2(
                id=line.id,
                catalog_id=line.catalog_id,
                packaging=line.packaging,
                sale_format=line.sale_format,
                quantity=line.quantity,
            )
            for line in current.quote.lines
            if isinstance(line, PhysicalUnitDraftQuoteLineV2)
        )
        batches = await self.repository.lock_physical_batches(
            tenant_id, sale.branch_id, sorted({line.catalog_id for line in physical}, key=str)
        )
        try:
            plan = plan_unit_cart_stock_v2(
                lines=physical,
                batches=batches,
                tenant_id=tenant_id,
                branch_id=sale.branch_id,
                today=today,
            )
        except ValueError as error:
            raise BusinessRuleError("Insufficient compatible physical stock for cart") from error
        # Stock locks may wait while access, register or shift status changes.
        await cart_service.lock_editable_sale(tenant_id, sale.id, actor_id, allowed_branch_ids)
        return MixedPhysicalCheckoutPreparation(saved, allocations, plan, today)

    async def _legacy_allocations(
        self, sale: Sale, items: tuple[SaleItem, ...], today: date
    ) -> tuple[LegacyCheckoutAllocation, ...]:
        allocations: list[LegacyCheckoutAllocation] = []
        inventory = InventoryRepository(self.repository.session)
        for catalog_id in sorted({item.catalog_id for item in items}, key=str):
            candidates = await inventory.fefo_candidates(
                tenant_id=sale.tenant_id,
                branch_id=sale.branch_id,
                catalog_id=catalog_id,
                include_expired=False,
                today=today,
                lock=True,
            )
            allocations.extend(
                plan_legacy_checkout_allocations(
                    items=tuple(item for item in items if item.catalog_id == catalog_id),
                    batches=candidates,
                    today=today,
                )
            )
        return tuple(allocations)
