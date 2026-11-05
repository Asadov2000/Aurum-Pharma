"""Plan a catalog's draft lines against one FEFO selection before writing anything."""

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from app.core.errors import BusinessRuleError
from app.domains.inventory.service import FefoPick
from app.domains.pos.models import SaleItem


@dataclass(frozen=True)
class DraftDemand:
    item: SaleItem | None
    quantity: Decimal
    discount: Decimal


@dataclass(frozen=True)
class DraftSlice:
    demand: DraftDemand
    pick: FefoPick
    quantity: Decimal
    unit_price: Decimal
    total_price: Decimal
    discount: Decimal
    first: bool


def plan_draft_fefo(
    demands: list[DraftDemand], picks: list[FefoPick], fallback_price: Decimal | None
) -> list[DraftSlice]:
    result: list[DraftSlice] = []
    batch_index = 0
    batch_used = Decimal("0")
    quantum = Decimal("0.01")
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        for demand in demands:
            remaining = demand.quantity
            portions: list[tuple[FefoPick, Decimal, Decimal, Decimal]] = []
            while remaining > 0:
                if batch_index >= len(picks):
                    raise BusinessRuleError("Insufficient stock for this catalog item")
                pick = picks[batch_index]
                price = pick.batch.sale_price or fallback_price or Decimal("0")
                if price <= 0:
                    raise BusinessRuleError("Catalog item has no valid sale price")
                take = min(remaining, pick.qty - batch_used)
                # Mixed checkout guards verify this established per-line rounding.
                gross = (take * price).quantize(quantum)
                portions.append((pick, take, price, gross))
                remaining -= take
                batch_used += take
                if batch_used == pick.qty:
                    batch_index += 1
                    batch_used = Decimal("0")
            gross_total = sum((part[3] for part in portions), Decimal("0"))
            if demand.discount < 0 or demand.discount > gross_total:
                raise BusinessRuleError("Draft discount exceeds the reallocated line amount")
            gross_seen = Decimal("0")
            discount_seen = Decimal("0")
            for index, (pick, quantity, price, gross) in enumerate(portions):
                gross_seen += gross
                discount_total = (
                    (demand.discount * gross_seen / gross_total).quantize(quantum)
                    if gross_total
                    else Decimal("0")
                )
                discount = discount_total - discount_seen
                discount_seen = discount_total
                result.append(
                    DraftSlice(
                        demand, pick, quantity, price, gross - discount, discount, index == 0
                    )
                )
    return result
