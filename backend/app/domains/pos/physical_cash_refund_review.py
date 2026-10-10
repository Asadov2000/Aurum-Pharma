"""Bind a reviewed selection to immutable checkout facts and trusted return cursors."""

import json
from collections.abc import Mapping
from hashlib import sha256
from uuid import UUID

from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection


def physical_refund_revision(
    checkout: PhysicalCheckoutResultV3,
    cursors: Mapping[UUID, int],
    selections: tuple[PhysicalReturnSelection, ...],
) -> str:
    payload = {
        "version": 1,
        "parent_sale_id": str(checkout.sale_id),
        "original_operation_id": str(checkout.operation_id),
        "cursors": [
            [str(line.id), cursors.get(line.id, 0)]
            for line in sorted(checkout.lines, key=lambda line: str(line.id))
        ],
        "selections": [[str(line.line_id), line.sale_format, line.quantity] for line in selections],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()
