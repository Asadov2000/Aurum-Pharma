"""Pure physical cart fingerprints and pack-state wire representations."""

from __future__ import annotations

import json
from decimal import Decimal
from hashlib import sha256
from uuid import UUID

from app.domains.inventory.unit_allocation_v2 import UnitCartLineV2
from app.domains.inventory.unit_stock_v2 import OpenedPackV2, UnitPackagingV2


def physical_cart_fingerprint(
    tenant_id: UUID,
    branch_id: UUID,
    lines: tuple[UnitCartLineV2, ...],
    discounts: tuple[Decimal, ...] | None = None,
) -> str:
    payload = [
        "aurum-unit-cart-v2:1" if discounts is None else "aurum-unit-cart-v2:retail:1",
        str(tenant_id),
        str(branch_id),
        [
            [
                str(line.id),
                str(line.catalog_id),
                line.packaging.version,
                line.packaging.unit,
                line.packaging.units_per_pack,
                line.packaging.units_per_blister,
                line.packaging.dispense_step,
                line.sale_format,
                line.quantity,
            ]
            + ([] if discounts is None else [format(discounts[position], ".2f")])
            for position, line in enumerate(lines)
        ],
    ]
    return sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def physical_pack_state(pack: OpenedPackV2 | None) -> dict[str, object]:
    if pack is None:
        return {"intact_blisters": [], "opened_blisters": [], "loose_units": 0}
    return pack.model_dump(mode="json", exclude={"id", "opened_at"})


def full_physical_pack_state(packaging: UnitPackagingV2) -> dict[str, object]:
    size = packaging.units_per_blister
    return {
        "intact_blisters": list(range(1, packaging.units_per_pack // size + 1)) if size else [],
        "opened_blisters": [],
        "loose_units": 0 if size else packaging.units_per_pack,
    }
