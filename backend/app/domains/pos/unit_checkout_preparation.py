"""Transient combined checkout checks, never a payment, reservation or committed sale."""

from dataclasses import dataclass
from datetime import date
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.exc import DBAPIError

from app.core.errors import AurumError, BusinessRuleError, ConflictError, NotFoundError
from app.core.time import utc_now
from app.domains.foundation.repository import FoundationRepository
from app.domains.inventory.unit_allocation_v2 import (
    UnitCartLineV2,
    UnitCartStockPlanV2,
    plan_unit_cart_stock_v2,
)
from app.domains.inventory.unit_stock import UnitStockBatch, UnitStockShortage
from app.domains.pos.unit_cart_quote import PhysicalUnitDraftQuoteLineV2, UnitDraftQuoteLineV2
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


@dataclass(frozen=True)
class UnitPhysicalCheckoutPreparation:
    """Transient physical-only check, never a reservation or input for posting."""

    confirmation: SavedCashIntentV2
    stock_plan: UnitCartStockPlanV2
    stock_date: date
    state: Literal["checked_physical_draft"] = "checked_physical_draft"


class UnitCheckoutPreparationService:
    def __init__(self, repository: UnitCheckoutRepository) -> None:
        self.repository = repository
        self.cart = UnitCartRepository(repository.session)

    async def prepare_physical(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
        request: CashConfirmationRequestV2,
    ) -> UnitPhysicalCheckoutPreparation:
        """Recheck saved cash, prescriptions and actual physical stock together.

        The caller owns the transaction. Checks expire when its locks are released;
        eventual checkout must repeat them atomically with payment and stock posting.
        Legacy SaleItem positions need their own allocation and are explicitly rejected.
        """
        request = CashConfirmationRequestV2.model_validate(request)
        try:
            return await self._prepare_physical(
                tenant_id=tenant_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
                request=request,
            )
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) == "55P03":
                raise ConflictError("Checkout stock is busy; retry in a new transaction") from error
            raise

    async def _prepare_physical(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
        request: CashConfirmationRequestV2,
    ) -> UnitPhysicalCheckoutPreparation:
        async with self.repository.session.begin_nested():
            sale = await UnitCartService(self.cart).lock_editable_sale(
                tenant_id, request.sale_id, actor_id, allowed_branch_ids
            )
            if sale.is_test:
                raise BusinessRuleError("Physical test sales are not supported yet")
            units = await self.cart.list_items(tenant_id, sale.id)
            if not units or any(item.physical_packaging is None for item in units):
                raise BusinessRuleError("Physical checkout requires physical unit cart positions")
            if await self.cart.list_legacy_items(tenant_id, sale.id):
                raise BusinessRuleError("Mixed legacy and physical checkout is not available yet")
            catalog_ids = sorted({item.catalog_id for item in units}, key=str)
            for catalog_id in catalog_ids:
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
            lines: list[UnitCartLineV2] = []
            for line in current.quote.lines:
                if not isinstance(line, PhysicalUnitDraftQuoteLineV2):
                    raise BusinessRuleError("Physical quote contains an incompatible position")
                lines.append(
                    UnitCartLineV2(
                        id=line.id,
                        catalog_id=line.catalog_id,
                        packaging=line.packaging,
                        sale_format=line.sale_format,
                        quantity=line.quantity,
                    )
                )
            today = await self._physical_stock_date(tenant_id)
            batches = await self.repository.lock_physical_batches(
                tenant_id, sale.branch_id, catalog_ids
            )
            try:
                plan = plan_unit_cart_stock_v2(
                    lines=tuple(lines),
                    batches=batches,
                    tenant_id=tenant_id,
                    branch_id=sale.branch_id,
                    today=today,
                )
            except ValueError as error:
                raise BusinessRuleError(
                    "Insufficient compatible physical stock for cart"
                ) from error
            return UnitPhysicalCheckoutPreparation(
                confirmation=saved, stock_plan=plan, stock_date=today
            )

    async def _physical_stock_date(self, tenant_id: UUID) -> date:
        settings = await FoundationRepository(self.repository.session).get_settings(tenant_id)
        timezone_name = settings.report_timezone if settings is not None else "Asia/Dushanbe"
        try:
            return utc_now().astimezone(ZoneInfo(timezone_name)).date()
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise AurumError("Tenant report timezone is invalid") from error

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
