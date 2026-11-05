"""Recheck current native cart, Rx and stock; never reserve, authorize or post."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from typing import Literal
from uuid import UUID

from app.core.errors import BusinessRuleError, ConflictError
from app.domains.inventory.unit_allocation_v2 import (
    UnitCartLineV2,
    UnitCartStockPlanV2,
    plan_unit_cart_stock_v2,
)
from app.domains.inventory.unit_dispensing_v2_retail import quote_retail_line_v2
from app.domains.inventory.unit_stock_v2 import to_base_units_v2
from app.domains.pos.edge_retail_cash_draft import EdgeRetailCashDraftV1
from app.domains.pos.edge_retail_cash_inputs import EdgeRetailCatalogInputV1
from app.domains.pos.legacy_checkout_stock import (
    LegacyCheckoutAllocation,
    plan_legacy_checkout_allocations,
)
from app.domains.pos.unit_cart_quote import MixedCartQuoteV2, PhysicalUnitDraftQuoteLineV2
from app.domains.pos.unit_contracts import EXACT_CONTEXT, LegacyResultLineV2


@dataclass(frozen=True)
class EdgeRetailCashCheckedDraftV1:
    """Facts expire with caller locks and are not an execution capability."""

    quote: MixedCartQuoteV2
    physical_stock_plan: UnitCartStockPlanV2
    legacy_allocations: tuple[LegacyCheckoutAllocation, ...]
    stock_date: date
    state: Literal["checked_current_draft_not_executed"] = "checked_current_draft_not_executed"


def _catalogs(draft: EdgeRetailCashDraftV1) -> dict[UUID, EdgeRetailCatalogInputV1]:
    catalogs = {catalog.id: catalog for catalog in draft.inputs.catalogs}
    current_catalogs = {row.catalog_id for row in draft.legacy_items} | {
        row.catalog_id for row in draft.unit_items
    }
    if current_catalogs != set(catalogs):
        raise ConflictError("Current native cart catalogs differ from the saved confirmation")
    if not draft.unit_items or any(item.physical_packaging is None for item in draft.unit_items):
        raise BusinessRuleError("Native retail cash requires physical unit positions")
    mixed = draft.inputs.preparation.kind == "mixed_cash"
    if bool(draft.legacy_items) != mixed:
        raise BusinessRuleError("Native current draft differs from the requested checkout kind")
    for catalog in catalogs.values():
        if (
            not catalog.is_active
            or catalog.deleted_at is not None
            or catalog.currency != "TJS"
            or catalog.dispensing_type == "special"
        ):
            raise BusinessRuleError("Native current cart contains an unavailable medicine")
    return catalogs


def _quote(draft: EdgeRetailCashDraftV1) -> MixedCartQuoteV2:
    catalogs = _catalogs(draft)
    lines: list[LegacyResultLineV2 | PhysicalUnitDraftQuoteLineV2] = []
    for row in draft.legacy_items:
        if row.parent_sale_item_id is not None or row.currency != "TJS":
            raise BusinessRuleError("Legacy current row has incompatible retail facts")
        with localcontext(EXACT_CONTEXT):
            gross = (row.unit_price * row.quantity).quantize(Decimal("0.01"))
        lines.append(
            LegacyResultLineV2(
                inventory_kind="legacy_pack",
                id=row.id,
                position=len(lines) + 1,
                catalog_id=row.catalog_id,
                batch_id=row.batch_id,
                quantity=row.quantity,
                original_retail_unit_price=row.unit_price,
                gross_amount=gross,
                discount_amount=row.discount_amount,
                net_amount=row.total_price,
            )
        )
    for item in draft.unit_items:
        if item.physical_packaging is None:
            raise BusinessRuleError("Native current cart requires physical packaging")
        physical = UnitCartLineV2(
            id=item.id,
            catalog_id=item.catalog_id,
            packaging=item.physical_packaging,
            sale_format=item.sale_format,
            quantity=item.quantity,
        )
        amounts = quote_retail_line_v2(physical, catalogs[item.catalog_id], item.discount_amount)
        lines.append(
            PhysicalUnitDraftQuoteLineV2(
                id=item.id,
                position=len(lines) + 1,
                catalog_id=item.catalog_id,
                packaging=physical.packaging,
                sale_format=physical.sale_format,
                quantity=physical.quantity,
                base_units=to_base_units_v2(
                    physical.packaging, sale_format=physical.sale_format, quantity=physical.quantity
                ),
                original_retail_unit_price=amounts.original_retail_unit_price,
                gross_amount=amounts.gross_amount,
                discount_amount=amounts.discount_amount,
                net_amount=amounts.net_amount,
            )
        )
    with localcontext(EXACT_CONTEXT):
        total = sum((line.net_amount for line in lines), Decimal("0.00"))
    prepared = draft.inputs.preparation
    return MixedCartQuoteV2(
        tenant_id=prepared.scope.tenant_id,
        branch_id=prepared.scope.branch_id,
        sale_id=prepared.sale_id,
        register_id=prepared.scope.register_id,
        shift_id=prepared.shift_id,
        cashier_id=prepared.scope.user_id,
        lines=tuple(lines),
        total_amount=total,
    )


def _require_prescriptions(draft: EdgeRetailCashDraftV1) -> None:
    required_catalogs = {
        row.id for row in draft.inputs.catalogs if row.dispensing_type == "prescription"
    }
    if not required_catalogs or any(
        ref.sale_item_id is None and ref.unit_cart_item_id is None for ref in draft.rx_refs
    ):
        return
    units = {item.id for item in draft.unit_items if item.catalog_id in required_catalogs}
    legacy = {item.id for item in draft.legacy_items if item.catalog_id in required_catalogs}
    covered_units = {ref.unit_cart_item_id for ref in draft.rx_refs}
    covered_legacy = {ref.sale_item_id for ref in draft.rx_refs}
    if units - covered_units or legacy - covered_legacy:
        raise BusinessRuleError("Prescription details are required for all current Rx positions")


def check_edge_retail_cash_draft(draft: EdgeRetailCashDraftV1) -> EdgeRetailCashCheckedDraftV1:
    """Use database-origin current rows while the caller retains its transaction."""
    draft = EdgeRetailCashDraftV1.model_validate(draft.model_dump(mode="python"))
    try:
        quote = _quote(draft)
        if quote != draft.inputs.saved_quote:
            raise ConflictError("Saved cash confirmation differs from the current native cart")
        _require_prescriptions(draft)
        physical_lines = tuple(
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
        scope = draft.inputs.preparation.scope
        plan = plan_unit_cart_stock_v2(
            lines=physical_lines,
            batches=draft.inputs.physical_stock,
            tenant_id=scope.tenant_id,
            branch_id=scope.branch_id,
            today=draft.inputs.stock_date,
        )
        legacy = plan_legacy_checkout_allocations(
            items=draft.legacy_items,
            batches=draft.inputs.legacy_stock,
            today=draft.inputs.stock_date,
        )
    except (ValueError, ArithmeticError) as error:
        raise BusinessRuleError("Native current cart has invalid amounts or stock") from error
    return EdgeRetailCashCheckedDraftV1(quote, plan, legacy, draft.inputs.stock_date)
