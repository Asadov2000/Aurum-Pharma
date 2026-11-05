"""Compare locked mixed draft rows with their immutable payment-plan snapshot."""

from decimal import Decimal, localcontext
from uuid import UUID

from pydantic import ValidationError

from app.core.errors import ConflictError
from app.domains.catalog.models import TenantCatalog
from app.domains.inventory.unit_allocation_v2 import UnitCartLineV2
from app.domains.inventory.unit_dispensing_v2_retail import quote_retail_line_v2
from app.domains.inventory.unit_stock_v2 import UnitPackagingV2
from app.domains.pos.models import SaleItem
from app.domains.pos.unit_cart_models import UnitCartItem
from app.domains.pos.unit_cart_quote import MixedCartQuoteV2, PhysicalUnitDraftQuoteLineV2
from app.domains.pos.unit_contracts import EXACT_CONTEXT


def require_mixed_electronic_source(
    quote: MixedCartQuoteV2,
    legacy: tuple[SaleItem, ...],
    units: tuple[UnitCartItem, ...],
    catalogs: dict[UUID, TenantCatalog],
) -> None:
    """No private database helper, repricing of receipts or relaxation of cart locks."""
    quoted_legacy = tuple(line for line in quote.lines if line.inventory_kind == "legacy_pack")
    quoted_units = tuple(
        line for line in quote.lines if isinstance(line, PhysicalUnitDraftQuoteLineV2)
    )
    if tuple(row.id for row in legacy) != tuple(line.id for line in quoted_legacy):
        raise ConflictError("Bound legacy cart differs from its payment plan")
    if tuple(row.id for row in units) != tuple(line.id for line in quoted_units):
        raise ConflictError("Bound physical cart differs from its mixed payment plan")
    for row, line in zip(legacy, quoted_legacy, strict=True):
        with localcontext(EXACT_CONTEXT):
            gross = (row.unit_price * row.qty).quantize(Decimal("0.01"))
        if (
            row.tenant_id,
            row.sale_id,
            row.parent_sale_item_id,
            row.currency,
            row.catalog_id,
            row.batch_id,
            row.qty,
            row.unit_price,
            gross,
            row.discount_amount,
            row.total_price,
        ) != (
            quote.tenant_id,
            quote.sale_id,
            None,
            "TJS",
            line.catalog_id,
            line.batch_id,
            line.quantity,
            line.original_retail_unit_price,
            line.gross_amount,
            line.discount_amount,
            line.net_amount,
        ):
            raise ConflictError("Bound legacy retail facts differ from their payment plan")
    for item, physical_line in zip(units, quoted_units, strict=True):
        _require_physical_source(quote, item, physical_line, catalogs[physical_line.catalog_id])


def _require_physical_source(
    quote: MixedCartQuoteV2,
    item: UnitCartItem,
    line: PhysicalUnitDraftQuoteLineV2,
    catalog: TenantCatalog,
) -> None:
    try:
        packaging = UnitPackagingV2.model_validate(item.physical_packaging)
    except ValidationError as error:
        raise ConflictError("Bound physical packaging differs from its mixed plan") from error
    if (
        item.tenant_id,
        item.sale_id,
        item.branch_id,
        item.catalog_id,
        item.sale_format,
        item.quantity,
        item.discount_amount,
        packaging,
    ) != (
        quote.tenant_id,
        quote.sale_id,
        quote.branch_id,
        line.catalog_id,
        line.sale_format,
        line.quantity,
        line.discount_amount,
        line.packaging,
    ):
        raise ConflictError("Bound physical cart differs from its mixed payment plan")
    amounts = quote_retail_line_v2(
        UnitCartLineV2(
            id=line.id,
            catalog_id=line.catalog_id,
            packaging=line.packaging,
            sale_format=line.sale_format,
            quantity=line.quantity,
        ),
        catalog,
        line.discount_amount,
    )
    if (
        amounts.original_retail_unit_price,
        amounts.gross_amount,
        amounts.discount_amount,
        amounts.net_amount,
    ) != (
        line.original_retail_unit_price,
        line.gross_amount,
        line.discount_amount,
        line.net_amount,
    ):
        raise ConflictError("Bound physical prices changed; reconcile the payment first")
