"""Pure physical snapshot conversion, independent of ledger writers and incoming."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domains.inventory.unit_stock_v2 import OpenedPackV2, UnitPackagingV2, UnitStockBatchV2

if TYPE_CHECKING:
    from app.domains.inventory.unit_ledger_v2_models import UnitStockLotV2, UnitStockOpenedPackV2


def physical_stock_snapshots_v2(
    lots: list[UnitStockLotV2], packs: list[UnitStockOpenedPackV2]
) -> tuple[UnitStockBatchV2, ...]:
    """Map locked database facts without deriving availability or financial amounts."""
    return tuple(
        UnitStockBatchV2(
            id=lot.id,
            tenant_id=lot.tenant_id,
            branch_id=lot.branch_id,
            catalog_id=lot.catalog_id,
            expires_at=lot.expires_at,
            created_at=lot.created_at,
            packaging=UnitPackagingV2.model_validate(lot.packaging),
            closed_packs=lot.closed_packs,
            is_blocked=lot.is_blocked,
            opened_packs=tuple(
                OpenedPackV2.model_validate(
                    {**pack.state, "id": pack.id, "opened_at": pack.opened_at}
                )
                for pack in packs
                if pack.lot_id == lot.id
            ),
        )
        for lot in lots
    )
