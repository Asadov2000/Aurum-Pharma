"""Recheck a frozen physical payment plan without editing its cart."""

from uuid import UUID

from app.core.errors import BusinessRuleError, ConflictError
from app.domains.catalog.models import TenantCatalog
from app.domains.inventory.unit_dispensing_v2_retail import (
    UnitPricedCartLineV2,
    quote_retail_line_v2,
)
from app.domains.inventory.unit_stock_v2 import UnitPackagingV2
from app.domains.pos.models import Sale
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.domains.pos.unit_cart_quote import PhysicalUnitDraftQuoteLineV2
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_checkout_repository import UnitCheckoutRepository
from app.domains.pos.unit_payment_intent_schemas import SavedUnitPaymentIntent
from app.domains.pos.unit_prescription_repository import UnitPrescriptionRepository


async def prepare_electronic_physical_cart(
    cart: UnitCartRepository, sale: Sale, saved: SavedUnitPaymentIntent
) -> tuple[UnitPricedCartLineV2, ...]:
    """Caller holds the original sale lock; the persisted binding freezes rows."""
    if sale.status != "draft" or sale.is_test or sale.sale_type != "sale":
        raise BusinessRuleError("Electronic physical checkout requires a real sale draft")
    shift = await cart.lock_shift(sale.tenant_id, sale.shift_id)
    if shift is None or (
        shift.status,
        shift.branch_id,
        shift.register_id,
        shift.opened_by_user_id,
        shift.currency,
    ) != ("open", sale.branch_id, sale.register_id, sale.cashier_user_id, "TJS"):
        raise BusinessRuleError("Electronic checkout requires the cashier's open shift")
    if not await cart.active_register(sale.tenant_id, sale.branch_id, sale.register_id):
        raise BusinessRuleError("Electronic checkout register is inactive")
    quoted = tuple(
        line for line in saved.plan.quote.lines if isinstance(line, PhysicalUnitDraftQuoteLineV2)
    )
    if not quoted or len(quoted) != len(saved.plan.quote.lines):
        raise BusinessRuleError("Electronic checkout currently supports physical positions only")
    if await cart.list_legacy_items(sale.tenant_id, sale.id):
        raise BusinessRuleError("Mixed electronic checkout is not supported yet")
    items = await cart.list_items(sale.tenant_id, sale.id)
    if tuple(item.id for item in items) != tuple(line.id for line in quoted):
        raise ConflictError("Bound physical cart differs from its payment plan")
    required: set[UUID] = set()
    prices = UnitCheckoutRepository(cart.session)
    lines: list[UnitPricedCartLineV2] = []
    catalogs: dict[UUID, TenantCatalog] = {}
    for catalog_id in sorted({line.catalog_id for line in quoted}, key=str):
        catalog = await prices.lock_checkout_catalog(sale.tenant_id, catalog_id)
        if catalog is None or not catalog.is_active or catalog.deleted_at is not None:
            raise BusinessRuleError("Electronic checkout contains an unavailable medicine")
        if catalog.currency != "TJS" or catalog.dispensing_type == "special":
            raise BusinessRuleError("Electronic checkout contains an unsupported medicine")
        catalogs[catalog_id] = catalog
    for item, quoted_line in zip(items, quoted, strict=True):
        if item.physical_packaging is None or (
            item.catalog_id,
            item.sale_format,
            item.quantity,
            item.discount_amount,
            UnitPackagingV2.model_validate(item.physical_packaging),
        ) != (
            quoted_line.catalog_id,
            quoted_line.sale_format,
            quoted_line.quantity,
            quoted_line.discount_amount,
            quoted_line.packaging,
        ):
            raise ConflictError("Bound physical cart differs from its payment plan")
        line = UnitPricedCartLineV2(
            id=quoted_line.id,
            catalog_id=quoted_line.catalog_id,
            packaging=quoted_line.packaging,
            sale_format=quoted_line.sale_format,
            quantity=quoted_line.quantity,
            discount_amount=quoted_line.discount_amount,
        )
        catalog = catalogs[line.catalog_id]
        amounts = quote_retail_line_v2(line, catalog, line.discount_amount)
        if (
            amounts.original_retail_unit_price,
            amounts.gross_amount,
            amounts.discount_amount,
            amounts.net_amount,
        ) != (
            quoted_line.original_retail_unit_price,
            quoted_line.gross_amount,
            quoted_line.discount_amount,
            quoted_line.net_amount,
        ):
            raise ConflictError("Bound physical prices changed; reconcile the payment first")
        if catalog.dispensing_type == "prescription":
            required.add(line.id)
        lines.append(line)
    await _require_prescriptions(cart, sale, required)
    await POSService(POSRepository(cart.session))._validate_pos_payment_configuration(
        tenant_id=sale.tenant_id,
        requested_methods={part.method for part in saved.plan.request.payments},
    )
    await cart.assert_actor(sale.tenant_id, sale.cashier_user_id, sale.branch_id)
    return tuple(lines)


async def _require_prescriptions(cart: UnitCartRepository, sale: Sale, required: set[UUID]) -> None:
    if not required:
        return
    references = await UnitPrescriptionRepository(cart.session).references(sale.tenant_id, sale.id)
    if any(legacy_id is None and unit_id is None for legacy_id, unit_id in references):
        return
    covered = {unit_id for _, unit_id in references if unit_id is not None}
    if required - covered:
        raise BusinessRuleError("Prescription details are required for all Rx physical positions")
