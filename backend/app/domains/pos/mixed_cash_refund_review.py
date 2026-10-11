"""Review revision over immutable mixed source and all trusted return cursors."""

import json
from collections.abc import Mapping
from decimal import Decimal
from hashlib import sha256
from uuid import UUID

from app.domains.pos.mixed_documents import LegacyResultLineV4, MixedCheckoutResultV4
from app.domains.pos.mixed_return_allocation import LegacyReturnSelection, MixedReturnSelection


def mixed_refund_revision(
    checkout: MixedCheckoutResultV4,
    legacy_cursors: Mapping[UUID, Decimal],
    physical_cursors: Mapping[UUID, int],
    selections: tuple[MixedReturnSelection, ...],
) -> str:
    payload = {
        "version": 1,
        "parent_sale_id": str(checkout.sale_id),
        "original_operation_id": str(checkout.operation_id),
        "cursors": [
            [
                str(line.id),
                line.inventory_kind,
                (
                    format(legacy_cursors.get(line.id, Decimal("0")), ".3f")
                    if isinstance(line, LegacyResultLineV4)
                    else physical_cursors.get(line.id, 0)
                ),
            ]
            for line in sorted(checkout.lines, key=lambda line: str(line.id))
        ],
        "selections": [
            (
                [str(selection.line_id), "legacy_pack", format(selection.quantity, ".3f")]
                if isinstance(selection, LegacyReturnSelection)
                else [
                    str(selection.line_id),
                    "physical_unit",
                    selection.sale_format,
                    selection.quantity,
                ]
            )
            for selection in sorted(selections, key=lambda selection: str(selection.line_id))
        ],
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
