"""Locked native inputs, not a quote, reservation, payment or execution port.

Only the dispatcher establishes database identity. The caller retains its
transaction and locks; these immutable Python values cannot retain a lock.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.catalog.unit_sales import UnitSalePreparation
from app.domains.inventory.unit_stock_v2 import UnitStockBatchV2
from app.domains.pos.edge_retail_cash import EdgeRetailCashCommandV1
from app.domains.pos.edge_retail_cash_repository import (
    EdgeRetailCashPreparationV1,
    preparation_payload,
)
from app.domains.pos.unit_cart_quote import (
    MixedCartQuoteV2,
    PhysicalUnitDraftQuoteLineV2,
    UnitDraftQuoteLineV2,
)
from app.domains.pos.unit_contracts import FrozenContract, Money
from app.domains.sync.offline_auth import OfflineAuthContractError

INPUTS_PROFILE = "retail_cash.inputs.v1"
MAX_INPUT_BYTES = 1_048_576


class EdgeRetailCatalogInputV1(FrozenContract):
    id: UUID
    tenant_id: UUID
    base_price: Money | None
    unit_sale_preparation: UnitSalePreparation | None
    dispensing_type: Literal["otc", "prescription", "special"]
    currency: str
    is_active: bool = Field(strict=True)
    deleted_at: AwareDatetime | None


class EdgeRetailLegacyStockInputV1(FrozenContract):
    id: UUID
    tenant_id: UUID
    branch_id: UUID
    catalog_id: UUID
    expires_at: date
    created_at: AwareDatetime
    qty_remaining: Decimal = Field(ge=0, max_digits=14, decimal_places=3, allow_inf_nan=False)
    sale_price: Money | None
    is_blocked: bool = Field(strict=True)

    @field_validator("qty_remaining", mode="before")
    @classmethod
    def exact_quantity(cls, value: object) -> Decimal:
        if not isinstance(value, str | Decimal):
            raise ValueError("native quantity must be an exact decimal string")
        try:
            return Decimal(value)
        except InvalidOperation as error:
            raise ValueError("native quantity is not a decimal") from error


def _unique_ordered(ids: tuple[UUID, ...]) -> None:
    if ids != tuple(sorted(set(ids), key=str)):
        raise ValueError("native input identifiers must be unique and sorted")


def _utc(value: datetime | None) -> None:
    if value is not None and value.utcoffset() != timedelta(0):
        raise ValueError("native timestamps must use UTC")


class EdgeRetailCashInputsV1(FrozenContract):
    schema_version: Literal[1] = 1
    state: Literal["locked_inputs_not_executed"]
    preparation: EdgeRetailCashPreparationV1
    stock_date: date
    saved_quote: MixedCartQuoteV2
    catalogs: tuple[EdgeRetailCatalogInputV1, ...] = Field(min_length=1, max_length=200)
    physical_stock: tuple[UnitStockBatchV2, ...] = Field(max_length=4096)
    legacy_stock: tuple[EdgeRetailLegacyStockInputV1, ...] = Field(max_length=4096)

    @model_validator(mode="after")
    def exact_saved_scope_and_inputs(self) -> Self:
        scope, quote = self.preparation.scope, self.saved_quote
        if (
            quote.tenant_id != scope.tenant_id
            or quote.branch_id != scope.branch_id
            or quote.register_id != scope.register_id
            or quote.cashier_id != scope.user_id
            or quote.sale_id != self.preparation.sale_id
            or quote.shift_id != self.preparation.shift_id
            or quote.revision_hash != self.preparation.saved_confirmation.expected_revision_hash
            or quote.total_amount != self.preparation.saved_confirmation.expected_total_amount
        ):
            raise ValueError("native saved quote differs from preparation")
        physical_ids = {
            line.catalog_id
            for line in quote.lines
            if isinstance(line, PhysicalUnitDraftQuoteLineV2)
        }
        legacy_ids = {
            line.catalog_id for line in quote.lines if line.inventory_kind == "legacy_pack"
        }
        if (
            not physical_ids
            or any(isinstance(line, UnitDraftQuoteLineV2) for line in quote.lines)
            or (self.preparation.kind == "physical_cash" and legacy_ids)
            or (self.preparation.kind == "mixed_cash" and not legacy_ids)
        ):
            raise ValueError("native saved quote differs from checkout kind")
        if {row.id for row in self.catalogs} != physical_ids | legacy_ids:
            raise ValueError("native catalog inputs differ from saved quote")
        _unique_ordered(tuple(row.id for row in self.catalogs))
        for catalog in self.catalogs:
            if catalog.tenant_id != scope.tenant_id:
                raise ValueError("native catalog belongs to another tenant")
            _utc(catalog.deleted_at)
        for rows, allowed_ids in (
            (self.physical_stock, physical_ids),
            (self.legacy_stock, legacy_ids),
        ):
            _unique_ordered(tuple(row.id for row in rows))
            for row in rows:
                if (
                    row.tenant_id != scope.tenant_id
                    or row.branch_id != scope.branch_id
                    or row.catalog_id not in allowed_ids
                ):
                    raise ValueError("native stock belongs to another scope or catalog")
                _utc(row.created_at)
        packs = tuple(pack for lot in self.physical_stock for pack in lot.opened_packs)
        if len(packs) > 16_384 or len({pack.id for pack in packs}) != len(packs):
            raise ValueError("native opened-pack inputs exceed their unique bound")
        for lot in self.physical_stock:
            _unique_ordered(tuple(pack.id for pack in lot.opened_packs))
            for pack in lot.opened_packs:
                _utc(pack.opened_at)
        return self


def inputs_payload(command: EdgeRetailCashCommandV1) -> str:
    payload = json.loads(preparation_payload(command))
    payload["profile"] = INPUTS_PROFILE
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class EdgeRetailCashInputsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_inputs(self, command: EdgeRetailCashCommandV1) -> EdgeRetailCashInputsV1:
        command = EdgeRetailCashCommandV1.model_validate(command.model_dump(mode="python"))
        if not self.session.in_transaction():
            raise OfflineAuthContractError("Native inputs require a caller-owned transaction")
        payload = inputs_payload(command)
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
            raise OfflineAuthContractError("Native inputs did not return a JSON object")
        encoded = json.dumps(raw, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > MAX_INPUT_BYTES:
            raise OfflineAuthContractError("Native inputs exceed the complete response bound")
        result = EdgeRetailCashInputsV1.model_validate_json(encoded)
        prepared = result.preparation
        if (
            prepared.scope != command.scope
            or prepared.kind != command.kind
            or prepared.sale_id != command.confirmation.sale_id
            or prepared.saved_confirmation != command.confirmation
        ):
            raise OfflineAuthContractError("Native inputs differ from the requested command")
        return result
