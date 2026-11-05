"""Current native draft facts; neither a reservation nor permission to post a sale."""

from __future__ import annotations

import hashlib
import json
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory.unit_stock_v2 import SaleFormatV2, UnitPackagingV2
from app.domains.pos.edge_retail_cash import EdgeRetailCashCommandV1
from app.domains.pos.edge_retail_cash_inputs import (
    MAX_INPUT_BYTES,
    EdgeRetailCashInputsV1,
    inputs_payload,
)
from app.domains.pos.unit_contracts import (
    FrozenContract,
    LegacyQuantity,
    Money,
    Position,
    WholeQuantity,
)
from app.domains.sync.offline_auth import OfflineAuthContractError

DRAFT_PROFILE = "retail_cash.draft.v1"


class EdgeRetailCurrentUnitItemV1(FrozenContract):
    id: UUID
    tenant_id: UUID
    branch_id: UUID
    sale_id: UUID
    catalog_id: UUID
    position: Position
    sale_format: SaleFormatV2
    physical_packaging: UnitPackagingV2 | None
    quantity: WholeQuantity
    discount_amount: Money


class EdgeRetailCurrentLegacyItemV1(FrozenContract):
    id: UUID
    tenant_id: UUID
    sale_id: UUID
    catalog_id: UUID
    batch_id: UUID
    position: int = Field(strict=True)
    quantity: LegacyQuantity
    unit_price: Money
    discount_amount: Money
    total_price: Money
    currency: str
    parent_sale_item_id: UUID | None

    @property
    def qty(self) -> LegacyQuantity:
        return self.quantity


class EdgeRetailPrescriptionReferenceV1(FrozenContract):
    sale_item_id: UUID | None
    unit_cart_item_id: UUID | None


class EdgeRetailCashDraftV1(FrozenContract):
    schema_version: Literal[1] = 1
    state: Literal["current_draft_not_executed"]
    inputs: EdgeRetailCashInputsV1
    unit_items: tuple[EdgeRetailCurrentUnitItemV1, ...] = Field(max_length=200)
    legacy_items: tuple[EdgeRetailCurrentLegacyItemV1, ...] = Field(max_length=200)
    rx_refs: tuple[EdgeRetailPrescriptionReferenceV1, ...] = Field(max_length=4096)

    @model_validator(mode="after")
    def scoped_current_rows(self) -> Self:
        prepared = self.inputs.preparation
        scope = prepared.scope
        rows: tuple[EdgeRetailCurrentLegacyItemV1 | EdgeRetailCurrentUnitItemV1, ...] = (
            *self.legacy_items,
            *self.unit_items,
        )
        if not 1 <= len(rows) <= 200 or len({row.id for row in rows}) != len(rows):
            raise ValueError("native current draft must have unique bounded positions")
        for row in rows:
            if row.tenant_id != scope.tenant_id or row.sale_id != prepared.sale_id:
                raise ValueError("native current row belongs to another sale or tenant")
        if any(row.branch_id != scope.branch_id for row in self.unit_items):
            raise ValueError("native current unit row belongs to another branch")
        unit_positions = tuple(row.position for row in self.unit_items)
        if unit_positions != tuple(sorted(set(unit_positions))):
            raise ValueError("native current unit rows must have unique ordered positions")
        legacy_order = tuple((row.position, str(row.id)) for row in self.legacy_items)
        if legacy_order != tuple(sorted(legacy_order)):
            raise ValueError("native current legacy rows must be ordered")
        return self


def draft_payload(command: EdgeRetailCashCommandV1) -> str:
    payload = json.loads(inputs_payload(command))
    payload["profile"] = DRAFT_PROFILE
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class EdgeRetailCashDraftRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_draft(self, command: EdgeRetailCashCommandV1) -> EdgeRetailCashDraftV1:
        command = EdgeRetailCashCommandV1.model_validate(command.model_dump(mode="python"))
        if not self.session.in_transaction():
            raise OfflineAuthContractError(
                "Native current draft requires a caller-owned transaction"
            )
        if self.session.new or self.session.dirty or self.session.deleted:
            raise OfflineAuthContractError(
                "Persist pending changes before reading the native draft"
            )
        payload = draft_payload(command)
        with self.session.no_autoflush:
            raw = await self.session.scalar(
                text(
                    "SELECT public.dispatch_edge_cash_sale_v1("
                    ":operation_id, :activation_id, :writer_epoch, :cashier_user_id, "
                    "CAST(:payload AS jsonb), :request_hash)"
                ),
                {
                    "operation_id": command.confirmation.operation_id,
                    "activation_id": command.scope.activation_id,
                    "writer_epoch": command.scope.writer_epoch,
                    "cashier_user_id": command.scope.user_id,
                    "payload": payload,
                    "request_hash": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                },
            )
        if not isinstance(raw, dict):
            raise OfflineAuthContractError("Native current draft did not return a JSON object")
        encoded = json.dumps(raw, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > MAX_INPUT_BYTES:
            raise OfflineAuthContractError(
                "Native current draft exceeds the complete response bound"
            )
        result = EdgeRetailCashDraftV1.model_validate_json(encoded)
        prepared = result.inputs.preparation
        if (
            prepared.scope != command.scope
            or prepared.kind != command.kind
            or prepared.saved_confirmation != command.confirmation
        ):
            raise OfflineAuthContractError(
                "Native current draft differs from the requested command"
            )
        return result
