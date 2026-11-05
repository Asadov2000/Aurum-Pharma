"""Recheck a bound mixed electronic plan under locks without posting either stock."""

from dataclasses import dataclass
from datetime import date
from typing import Literal
from uuid import UUID

from app.core.errors import BusinessRuleError, ConflictError
from app.domains.catalog.models import TenantCatalog
from app.domains.inventory.unit_allocation_v2 import (
    UnitCartLineV2,
    UnitCartStockPlanV2,
    plan_unit_cart_stock_v2,
)
from app.domains.pos.mixed_checkout_preparation import (
    LegacyCheckoutAllocation,
    MixedPhysicalCheckoutPreparationService,
)
from app.domains.pos.mixed_electronic_checkout_source import require_mixed_electronic_source
from app.domains.pos.models import Sale
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.domains.pos.unit_cart_quote import MixedCartQuoteV2, PhysicalUnitDraftQuoteLineV2
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_checkout_preparation import UnitCheckoutPreparationService
from app.domains.pos.unit_checkout_repository import UnitCheckoutRepository
from app.domains.pos.unit_electronic_checkout_preparation import _require_open_register
from app.domains.pos.unit_payment_binding_repository import UnitPaymentBindingRepository
from app.domains.pos.unit_payment_intent_schemas import SavedUnitPaymentIntent
from app.domains.pos.unit_prescription_repository import UnitPrescriptionRepository


@dataclass(frozen=True)
class MixedElectronicCheckoutPreparation:
    """Transient lock-scoped stock checks, never payment or posting authorization."""

    legacy_allocations: tuple[LegacyCheckoutAllocation, ...]
    physical_stock_plan: UnitCartStockPlanV2
    stock_date: date
    state: Literal["checked_mixed_electronic_draft"] = "checked_mixed_electronic_draft"


def mixed_electronic_lines(quote: MixedCartQuoteV2) -> tuple[UnitCartLineV2, ...]:
    if {line.inventory_kind for line in quote.lines} != {"legacy_pack", "physical_unit"}:
        raise BusinessRuleError("Mixed electronic checkout requires legacy and physical positions")
    return tuple(
        UnitCartLineV2(
            id=line.id,
            catalog_id=line.catalog_id,
            packaging=line.packaging,
            sale_format=line.sale_format,
            quantity=line.quantity,
        )
        for line in quote.lines
        if isinstance(line, PhysicalUnitDraftQuoteLineV2)
    )


async def prepare_electronic_mixed_cart(
    cart: UnitCartRepository, sale: Sale, saved: SavedUnitPaymentIntent
) -> MixedElectronicCheckoutPreparation:
    """Caller owns the sale/intent locks, clean session and final transaction."""
    quote = saved.plan.quote
    physical = mixed_electronic_lines(quote)
    if (
        sale.status != "draft"
        or sale.is_test
        or sale.sale_type != "sale"
        or sale.parent_sale_id is not None
    ):
        raise BusinessRuleError("Mixed electronic checkout requires a real sale draft")
    if (
        sale.tenant_id,
        sale.id,
        sale.branch_id,
        sale.register_id,
        sale.shift_id,
        sale.cashier_user_id,
        sale.currency,
    ) != (
        quote.tenant_id,
        quote.sale_id,
        quote.branch_id,
        quote.register_id,
        quote.shift_id,
        quote.cashier_id,
        "TJS",
    ):
        raise ConflictError("Mixed electronic checkout scope differs from its plan")
    await _require_open_register(cart, sale)
    repository = UnitCheckoutRepository(cart.session)
    required_legacy, required_physical, catalogs = await _lock_catalogs(repository, sale, quote)
    legacy = await cart.list_legacy_items(sale.tenant_id, sale.id)
    units = await cart.list_items(sale.tenant_id, sale.id)
    require_mixed_electronic_source(quote, legacy, units, catalogs)
    await POSService(POSRepository(cart.session))._validate_pos_payment_configuration(
        tenant_id=sale.tenant_id,
        requested_methods={part.method for part in saved.plan.request.payments},
    )
    if required_legacy or required_physical:
        references = await UnitPrescriptionRepository(cart.session).references(
            sale.tenant_id, sale.id
        )
        global_reference = any(a is None and b is None for a, b in references)
        if not global_reference and (
            required_legacy - {a for a, _ in references if a is not None}
            or required_physical - {b for _, b in references if b is not None}
        ):
            raise BusinessRuleError("Prescription details are required for all mixed Rx positions")
    today = await UnitCheckoutPreparationService(repository)._physical_stock_date(sale.tenant_id)
    allocations = await MixedPhysicalCheckoutPreparationService(repository)._legacy_allocations(
        sale, legacy, today
    )
    batches = await repository.lock_physical_batches(
        sale.tenant_id, sale.branch_id, sorted({line.catalog_id for line in physical}, key=str)
    )
    try:
        plan = plan_unit_cart_stock_v2(
            lines=physical,
            batches=batches,
            tenant_id=sale.tenant_id,
            branch_id=sale.branch_id,
            today=today,
        )
    except ValueError as error:
        raise BusinessRuleError("Insufficient compatible physical stock for mixed cart") from error
    await _require_open_register(cart, sale)
    binding, cancellation = await UnitPaymentBindingRepository(cart.session).get_state(
        sale.tenant_id, saved.operation_id
    )
    if binding is None or cancellation is not None or binding.sale_id != sale.id:
        raise ConflictError("Mixed electronic checkout requires its active bound plan")
    await cart.assert_actor(sale.tenant_id, sale.cashier_user_id, sale.branch_id)
    return MixedElectronicCheckoutPreparation(allocations, plan, today)


async def _lock_catalogs(
    repository: UnitCheckoutRepository, sale: Sale, quote: MixedCartQuoteV2
) -> tuple[set[UUID], set[UUID], dict[UUID, TenantCatalog]]:
    required_legacy: set[UUID] = set()
    required_physical: set[UUID] = set()
    catalogs: dict[UUID, TenantCatalog] = {}
    for catalog_id in sorted({line.catalog_id for line in quote.lines}, key=str):
        catalog = await repository.lock_checkout_catalog(sale.tenant_id, catalog_id)
        if (
            catalog is None
            or not catalog.is_active
            or catalog.deleted_at is not None
            or catalog.currency != "TJS"
            or catalog.dispensing_type == "special"
        ):
            raise BusinessRuleError("Mixed electronic checkout contains an unavailable medicine")
        catalogs[catalog_id] = catalog
        if catalog.dispensing_type == "prescription":
            for line in quote.lines:
                if line.catalog_id == catalog_id:
                    if line.inventory_kind == "legacy_pack":
                        required_legacy.add(line.id)
                    else:
                        required_physical.add(line.id)
    return required_legacy, required_physical, catalogs
