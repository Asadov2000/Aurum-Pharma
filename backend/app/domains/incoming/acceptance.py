"""Read-side acceptance metadata and an opaque draft confirmation revision."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Sequence
from typing import Literal

from app.core.config import get_settings
from app.domains.incoming.models import IncomingDocument, IncomingItem

AcceptanceMode = Literal["legacy_pack", "physical_unit_v2"]
StockMode = Literal["legacy_pack", "prepared_unit_v1", "physical_unit_v2"]


def stock_mode(items: Sequence[IncomingItem]) -> StockMode | None:
    if not items:
        return None
    if all(item.created_unit_lot_v2_id is not None for item in items):
        return "physical_unit_v2"
    if all(item.created_unit_lot_id is not None for item in items):
        return "prepared_unit_v1"
    if all(item.created_batch_id is not None for item in items):
        return "legacy_pack"
    return None


def physical_receiving_ready(doc: IncomingDocument, items: Sequence[IncomingItem]) -> bool:
    return (
        doc.status == "draft"
        and bool(items)
        and all(
            item.unit_packaging_preparation is not None
            and item.unit_packaging_snapshot_v2 is not None
            and item.qty > 0
            and item.qty == item.qty.to_integral_value()
            for item in items
        )
    )


def acceptance_revision(doc: IncomingDocument, items: Sequence[IncomingItem]) -> str | None:
    if doc.status != "draft":
        return None
    # Include content as database timestamps use transaction time. A keyed digest
    # prevents guessing private purchase prices from a public confirmation token.
    payload = {
        "document": [
            str(doc.id),
            str(doc.tenant_id),
            str(doc.branch_id),
            str(doc.supplier_id),
            doc.document_number,
            str(doc.document_date),
            doc.notes,
            doc.document_file_path,
            str(doc.updated_at),
        ],
        "items": [
            [
                str(item.id),
                str(item.catalog_id),
                str(item.updated_at),
                item.batch_number,
                str(item.manufactured_at),
                str(item.expires_at),
                str(item.qty),
                str(item.purchase_price),
                str(item.sale_price),
                item.currency,
                item.unit_packaging_preparation,
                item.unit_packaging_snapshot_v2,
            ]
            for item in sorted(items, key=lambda item: item.id)
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    key = hmac.digest(get_settings().JWT_SECRET.encode(), b"incoming-acceptance-v1", "sha256")
    return hmac.new(key, encoded, hashlib.sha256).hexdigest()
