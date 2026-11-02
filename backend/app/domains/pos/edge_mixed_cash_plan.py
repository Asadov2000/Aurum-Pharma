"""Mixed retail quote plus physical oracle; PostgreSQL owns legacy allocation."""

from typing import Self

from pydantic import model_validator

from app.core.errors import BusinessRuleError
from app.domains.pos.edge_physical_cash_plan import (
    NativePhysicalMovementPlanV1,
    build_native_physical_movement_plan,
)
from app.domains.pos.edge_retail_cash_draft import EdgeRetailCashDraftV1
from app.domains.pos.edge_retail_cash_repricing import check_edge_retail_cash_draft


class NativeMixedCashPlanV1(NativePhysicalMovementPlanV1):
    @model_validator(mode="after")
    def both_stock_kinds(self) -> Self:
        if {line.inventory_kind for line in self.quote.lines} != {"legacy_pack", "physical_unit"}:
            raise ValueError("native mixed plan requires both legacy and physical positions")
        return self


def build_edge_mixed_cash_plan(draft: EdgeRetailCashDraftV1) -> NativeMixedCashPlanV1:
    """Recheck both ledgers from current rows; no caller plan authorizes posting."""
    draft = EdgeRetailCashDraftV1.model_validate(draft.model_dump(mode="python"))
    if (
        draft.inputs.preparation.kind != "mixed_cash"
        or not draft.legacy_items
        or not draft.unit_items
    ):
        raise BusinessRuleError("Native mixed execution requires both inventory kinds")
    checked = check_edge_retail_cash_draft(draft)
    return NativeMixedCashPlanV1.model_validate(
        build_native_physical_movement_plan(checked).model_dump(mode="python")
    )
