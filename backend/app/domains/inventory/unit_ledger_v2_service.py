"""Initialize saved incoming packaging; no dispensing or adoption of old stock."""

from __future__ import annotations

import json
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.incoming.models import IncomingDocument, IncomingItem
from app.domains.incoming.unit_preparation import validate_prepared_pack_quantity
from app.domains.inventory.unit_ledger_service import PreparedLotResult
from app.domains.inventory.unit_ledger_v2_models import (
    UnitStockEntryV2,
    UnitStockLotV2,
    UnitStockOperationV2,
)
from app.domains.inventory.unit_ledger_v2_repository import UnitLedgerV2Repository
from app.domains.inventory.unit_stock import UnitPackaging
from app.domains.inventory.unit_stock_v2 import UnitPackagingV2, to_base_units_v2
from app.domains.pos.unit_money import allocate_unit_amount


def _fingerprint(item: IncomingItem, document: IncomingDocument, packaging: UnitPackagingV2) -> str:
    # Fixed-order, versioned protocol shared with the private database validator.
    payload = [
        "aurum-unit-receive-v2:1",
        str(item.id),
        str(document.id),
        str(item.tenant_id),
        str(document.branch_id),
        str(item.catalog_id),
        packaging.version,
        packaging.unit,
        packaging.units_per_pack,
        packaging.units_per_blister,
        packaging.dispense_step,
        int(item.qty),
        format(item.purchase_price, ".2f"),
        item.expires_at.isoformat(),
        "TJS",
        "initialize",
        "pack",
    ]
    return sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


class PreparedUnitLedgerV2Service:
    def __init__(self, repo: UnitLedgerV2Repository) -> None:
        self.repo = repo

    async def _incoming_source(
        self, item_id: UUID, actor_id: UUID, allowed_branch_ids: set[UUID]
    ) -> tuple[IncomingItem, IncomingDocument, UnitPackagingV2]:
        source = await self.repo.incoming_source(item_id, actor_id)
        if source is None:
            raise NotFoundError("Incoming item not found")
        item, document = source
        if document.branch_id not in allowed_branch_ids:
            raise PermissionDeniedError("Branch access denied")
        if (
            document.status != "draft"
            or item.tenant_id != document.tenant_id
            or item.created_batch_id is not None
            or item.created_unit_lot_id is not None
            or item.created_unit_lot_v2_id is not None
            or item.unit_packaging_snapshot_v2 is None
            or item.unit_packaging_preparation is None
            or item.currency != "TJS"
            or document.currency != "TJS"
        ):
            raise BusinessRuleError("Version-two receiving requires an unclaimed prepared draft")
        packaging = UnitPackagingV2.model_validate(item.unit_packaging_snapshot_v2)
        legacy = UnitPackaging.model_validate(item.unit_packaging_preparation)
        if packaging.model_dump(exclude={"version", "units_per_blister"}) != legacy.model_dump(
            exclude={"version"}
        ):
            raise BusinessRuleError("Historical packaging snapshots disagree")
        validate_prepared_pack_quantity(legacy, item.qty)
        allocate_unit_amount(item.purchase_price, total_units=1, offset=0, quantity=1)
        return item, document, packaging

    async def _claim_incoming_lot(
        self, item_id: UUID, *, actor_id: UUID, allowed_branch_ids: set[UUID]
    ) -> UUID:
        item, document, packaging = await self._incoming_source(
            item_id, actor_id, allowed_branch_ids
        )
        operation_id = uuid5(NAMESPACE_URL, f"aurum-unit-incoming-v2:{item.tenant_id}:{item.id}")
        _, created = await self.repo.claim_operation_v2(
            UnitStockOperationV2(
                id=operation_id,
                tenant_id=item.tenant_id,
                branch_id=document.branch_id,
                catalog_id=item.catalog_id,
                source_item_id=item.id,
                packaging=packaging.model_dump(mode="json"),
                kind="initialize",
                sale_format="pack",
                quantity=int(item.qty),
                base_units=to_base_units_v2(packaging, quantity=int(item.qty), sale_format="pack"),
                request_hash=_fingerprint(item, document, packaging),
                created_by=actor_id,
            )
        )
        if not created:
            raise ConflictError("Version-two receiving command was already claimed")
        return operation_id

    async def _prepare_incoming_lot(
        self, item_id: UUID, *, actor_id: UUID, allowed_branch_ids: set[UUID]
    ) -> PreparedLotResult:
        item, document, packaging = await self._incoming_source(
            item_id, actor_id, allowed_branch_ids
        )
        operation_id = uuid5(NAMESPACE_URL, f"aurum-unit-incoming-v2:{item.tenant_id}:{item.id}")
        operation = await self.repo.get_operation_v2(item.tenant_id, operation_id)
        if (
            operation is None
            or operation.request_hash != _fingerprint(item, document, packaging)
            or await self.repo.has_entries_v2(item.tenant_id, operation_id)
        ):
            raise ConflictError("Version-two receiving requires a fresh matching source command")
        lot_id = uuid5(NAMESPACE_URL, f"aurum-unit-lot-v2:{item.tenant_id}:{operation_id}")
        await self.repo.insert_lot_v2(
            UnitStockLotV2(
                id=lot_id,
                tenant_id=item.tenant_id,
                branch_id=document.branch_id,
                catalog_id=item.catalog_id,
                source_item_id=item.id,
                packaging=packaging.model_dump(mode="json"),
                expires_at=item.expires_at,
                initial_closed_packs=int(item.qty),
                purchase_cost_per_pack=item.purchase_price,
                cost_version=1,
                closed_packs=0,
                created_by=actor_id,
            )
        )
        item.created_unit_lot_v2_id = lot_id
        await self.repo.session.flush()
        await self.repo.insert_entry_v2(
            UnitStockEntryV2(
                id=uuid4(),
                tenant_id=item.tenant_id,
                branch_id=document.branch_id,
                catalog_id=item.catalog_id,
                source_item_id=item.id,
                operation_id=operation_id,
                lot_id=lot_id,
                kind="initialize",
                closed_delta=int(item.qty),
                base_units=operation.base_units,
                created_by=actor_id,
            )
        )
        return PreparedLotResult(operation_id, lot_id, operation.base_units)
