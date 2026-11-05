"""Bounded immutable physical inventory facts; never operational stock commands."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator, model_validator

from app.domains.catalog.unit_sales import MAX_BASE_UNITS, UnitSalePreparation
from app.domains.inventory.unit_stock_v2 import OpenedBlisterV2
from app.domains.pos.retail_sale_event_storage import RetailSaleEventAnchor
from app.domains.pos.retail_sale_events import RetailSaleStreamPosition
from app.domains.pos.unit_contracts import FrozenContract
from app.domains.pos.unit_event_storage import _reject_nonfinite, _unique_object
from app.domains.sync.integrity import (
    canonical_json_bytes,
    canonical_json_hash,
    projection_stream_checksum,
    source_stream_checksum,
)
from app.domains.sync.physical_read_snapshot import Money, PhysicalLotRecord
from app.domains.sync.schemas import SyncEventEnvelope

EVENT_TYPE = "inventory.physical.changed.v5"
MAX_RECORD_BYTES = 1024 * 1024
StockSourceKind = Literal[
    "initialize",
    "legacy_transition",
    "dispense",
    "writeoff",
    "writeoff_cancelled",
    "block",
    "catalog_config",
    "baseline_required",
]


class PhysicalPackState(FrozenContract):
    intact_blisters: tuple[int, ...] = ()
    opened_blisters: tuple[OpenedBlisterV2, ...] = ()
    loose_units: int = Field(default=0, strict=True, ge=0, le=MAX_BASE_UNITS)

    @model_validator(mode="after")
    def unique_blisters(self) -> Self:
        numbers = self.intact_blisters + tuple(b.number for b in self.opened_blisters)
        if any(type(n) is not int or n < 1 for n in numbers) or len(set(numbers)) != len(numbers):
            raise ValueError("Physical blister identities must be positive and unique")
        return self


class PhysicalStockMovement(FrozenContract):
    id: UUID
    line_id: UUID | None = None
    entry_order: int = Field(strict=True, ge=0)
    lot_id: UUID
    catalog_id: UUID
    pack_id: UUID | None = None
    kind: Literal["pack_open", "pack_take", "blister_take", "unit_take", "writeoff"]
    closed_delta: int = Field(strict=True, le=0, ge=-MAX_BASE_UNITS)
    units_taken: int = Field(strict=True, ge=0, le=MAX_BASE_UNITS)
    sealed_start: int | None = Field(default=None, strict=True, ge=1)
    sealed_count: int | None = Field(default=None, strict=True, ge=1)
    before_state: PhysicalPackState | None = None
    after_state: PhysicalPackState | None = None
    pack_ordinal: int | None = Field(default=None, strict=True, ge=1)
    opened_at: AwareDatetime | None = None
    cost_unit_offset: int | None = Field(default=None, strict=True, ge=0, le=9999)
    cost_original_pack_cost: Money | None = None
    cost_amount: Money | None = None
    cost_version: Literal[1] | None = None

    @model_validator(mode="after")
    def physical_shape(self) -> Self:
        if self.kind == "pack_open":
            if self.closed_delta != -1 or self.units_taken != 0 or self.pack_id is None:
                raise ValueError("Physical opening must move one sealed pack without consumption")
        elif self.units_taken <= 0:
            raise ValueError("Physical consumption requires positive units")
        if self.pack_id is None:
            if (
                self.closed_delta >= 0
                or self.before_state is not None
                or self.after_state is not None
            ):
                raise ValueError("Sealed consumption must preserve its exact range")
            if self.sealed_start is None or self.sealed_count != -self.closed_delta:
                raise ValueError("Sealed consumption requires its historical pack range")
        elif (
            self.pack_ordinal is None
            or self.opened_at is None
            or self.before_state is None
            or self.after_state is None
        ):
            raise ValueError("Opened movement requires exact pack identity and before/after state")
        if self.kind != "pack_open" and (
            self.cost_version != 1
            or self.cost_original_pack_cost is None
            or self.cost_amount is None
            or self.cost_unit_offset is None
        ):
            raise ValueError("Physical consumption requires historical cost evidence")
        return self


class PhysicalStockConfig(FrozenContract):
    catalog_id: UUID
    revision: int = Field(strict=True, ge=1)
    base_price: Money | None
    preparation: UnitSalePreparation | None


class PhysicalStockBlock(FrozenContract):
    lot_id: UUID
    catalog_id: UUID
    is_blocked: bool = Field(strict=True)
    revision: int = Field(strict=True, ge=1)


class PhysicalStockInvalidation(FrozenContract):
    reason: Literal["legacy_physical_change"] = "legacy_physical_change"
    source_table: Literal[
        "unit_stock_operation", "unit_stock_entry", "unit_stock_lot", "unit_stock_opened_pack"
    ]
    source_id: UUID


class PhysicalStockChange(FrozenContract):
    schema_version: Literal[5] = 5
    event_id: UUID
    operation_id: UUID
    tenant_id: UUID
    branch_id: UUID
    source_kind: StockSourceKind
    source_id: UUID
    occurred_at: AwareDatetime
    source_owner: Literal["standalone", "checkout"] = "standalone"
    checkout_sale_id: UUID | None = None
    checkout_event_id: UUID | None = None
    initializations: tuple[PhysicalLotRecord, ...] = Field(default=(), max_length=1)
    movements: tuple[PhysicalStockMovement, ...] = Field(default=(), max_length=10_000)
    configuration: PhysicalStockConfig | None = None
    blocking: PhysicalStockBlock | None = None
    invalidation: PhysicalStockInvalidation | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def version_type(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("Physical event version must be an integer")
        return value

    @field_validator("occurred_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def source_shape(self) -> Self:
        linked = self.source_owner == "checkout"
        if linked != (self.checkout_sale_id is not None and self.checkout_event_id is not None):
            raise ValueError("Checkout inventory facts require their trusted financial binding")
        if not linked and (self.checkout_sale_id is not None or self.checkout_event_id is not None):
            raise ValueError("Standalone inventory facts cannot claim a checkout")
        if linked and self.source_kind != "dispense":
            raise ValueError("Only dispensing commands may belong to a checkout")
        expected = (
            self.source_kind in ("initialize", "legacy_transition"),
            self.source_kind in ("dispense", "writeoff"),
            self.source_kind == "catalog_config",
            self.source_kind == "block",
            self.source_kind == "baseline_required",
        )
        actual = (
            bool(self.initializations),
            bool(self.movements),
            self.configuration is not None,
            self.blocking is not None,
            self.invalidation is not None,
        )
        if expected != actual:
            raise ValueError("Physical facts do not match their source category")
        for lot in self.initializations:
            if lot.tenant_id != self.tenant_id or lot.branch_id != self.branch_id:
                raise ValueError("Physical initialization escapes its event scope")
        if [m.entry_order for m in self.movements] != list(range(len(self.movements))):
            raise ValueError("Physical movements must be complete and ordered")
        if len({m.id for m in self.movements}) != len(self.movements):
            raise ValueError("Physical movement identities must be unique")
        if self.source_kind == "writeoff" and any(m.kind != "writeoff" for m in self.movements):
            raise ValueError("Write-off source contains another movement kind")
        if self.source_kind == "dispense" and any(m.kind == "writeoff" for m in self.movements):
            raise ValueError("Dispensing source contains write-off effects")
        return self


class PhysicalStockEventAnchor(RetailSaleEventAnchor):
    """Independent protected stock evidence; never inferred from incoming bytes."""


def build_physical_stock_event(
    change: PhysicalStockChange, *, position: RetailSaleStreamPosition
) -> SyncEventEnvelope:
    change = PhysicalStockChange.model_validate(change.model_dump(mode="python"))
    position = RetailSaleStreamPosition.model_validate(position.model_dump(mode="python"))
    payload = change.model_dump(mode="json")
    digest = canonical_json_hash(payload)
    return SyncEventEnvelope(
        event_id=change.event_id,
        tenant_id=change.tenant_id,
        branch_id=change.branch_id,
        origin_node_id=position.origin_node_id,
        writer_epoch=position.writer_epoch,
        sequence=position.sequence,
        operation_id=change.operation_id,
        aggregate_type="physical_stock",
        aggregate_id=change.source_id,
        event_type=EVENT_TYPE,
        schema_version=5,
        occurred_at=change.occurred_at,
        payload=payload,
        payload_hash=digest,
        projection_hash=digest,
        stream_checksum=source_stream_checksum(
            previous_checksum=position.previous_source_checksum,
            event_id=change.event_id,
            tenant_id=change.tenant_id,
            branch_id=change.branch_id,
            origin_node_id=position.origin_node_id,
            writer_epoch=position.writer_epoch,
            sequence=position.sequence,
            operation_id=change.operation_id,
            aggregate_type="physical_stock",
            aggregate_id=change.source_id,
            event_type=EVENT_TYPE,
            schema_version=5,
            occurred_at=change.occurred_at,
            payload_hash=digest,
        ),
        projection_checksum=projection_stream_checksum(
            previous_checksum=position.previous_projection_checksum,
            origin_node_id=position.origin_node_id,
            writer_epoch=position.writer_epoch,
            sequence=position.sequence,
            sale_id=change.source_id,
            projection_hash=digest,
        ),
    )


def validate_physical_stock_event(
    envelope: SyncEventEnvelope,
    *,
    previous_source_checksum: str,
    previous_projection_checksum: str,
) -> PhysicalStockChange:
    change = PhysicalStockChange.model_validate(envelope.payload)
    expected = build_physical_stock_event(
        change,
        position=RetailSaleStreamPosition(
            origin_node_id=envelope.origin_node_id,
            writer_epoch=envelope.writer_epoch,
            sequence=envelope.sequence,
            previous_source_checksum=previous_source_checksum,
            previous_projection_checksum=previous_projection_checksum,
        ),
    )
    if canonical_json_bytes(expected.model_dump(mode="json")) != canonical_json_bytes(
        envelope.model_dump(mode="json")
    ):
        raise ValueError("Physical event differs from its canonical source facts")
    return change


def physical_stock_event_fingerprint(
    envelope: SyncEventEnvelope,
    *,
    previous_source_checksum: str,
    previous_projection_checksum: str,
) -> str:
    validate_physical_stock_event(
        envelope,
        previous_source_checksum=previous_source_checksum,
        previous_projection_checksum=previous_projection_checksum,
    )
    return canonical_json_hash(
        {
            "domain": "aurum-physical-stock-event",
            "schema_version": 5,
            "envelope": envelope.model_dump(mode="json"),
        }
    )


def build_physical_stock_event_anchor(
    envelope: SyncEventEnvelope,
    *,
    previous_source_checksum: str,
    previous_projection_checksum: str,
) -> PhysicalStockEventAnchor:
    return PhysicalStockEventAnchor(
        tenant_id=envelope.tenant_id,
        branch_id=envelope.branch_id,
        origin_node_id=envelope.origin_node_id,
        writer_epoch=envelope.writer_epoch,
        sequence=envelope.sequence,
        previous_source_checksum=previous_source_checksum,
        previous_projection_checksum=previous_projection_checksum,
        event_fingerprint=physical_stock_event_fingerprint(
            envelope,
            previous_source_checksum=previous_source_checksum,
            previous_projection_checksum=previous_projection_checksum,
        ),
    )


def _anchored(envelope: SyncEventEnvelope, anchor: PhysicalStockEventAnchor) -> PhysicalStockChange:
    expected = build_physical_stock_event_anchor(
        envelope,
        previous_source_checksum=anchor.previous_source_checksum,
        previous_projection_checksum=anchor.previous_projection_checksum,
    )
    if expected != PhysicalStockEventAnchor.model_validate(anchor.model_dump()):
        raise ValueError("Physical event differs from its independently trusted anchor")
    return PhysicalStockChange.model_validate(envelope.payload)


def encode_physical_stock_event(
    envelope: SyncEventEnvelope, *, anchor: PhysicalStockEventAnchor
) -> bytes:
    _anchored(envelope, anchor)
    record = canonical_json_bytes(
        {
            "record_kind": "physical_stock_event",
            "storage_version": 1,
            "envelope": envelope.model_dump(mode="json"),
        }
    )
    if len(record) > MAX_RECORD_BYTES:
        raise ValueError("Physical event exceeds its bounded record size")
    return record


def decode_physical_stock_event(
    record: bytes, *, anchor: PhysicalStockEventAnchor
) -> PhysicalStockChange:
    if type(record) is not bytes or not 0 < len(record) <= MAX_RECORD_BYTES:
        raise ValueError("Physical event requires bounded nonempty bytes")
    try:
        raw = json.loads(
            record.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_nonfinite,
        )
    except RecursionError as error:
        raise ValueError("Physical event nesting exceeds its bound") from error
    if (
        not isinstance(raw, dict)
        or set(raw) != {"record_kind", "storage_version", "envelope"}
        or raw["record_kind"] != "physical_stock_event"
        or type(raw["storage_version"]) is not int
        or raw["storage_version"] != 1
    ):
        raise ValueError("Physical event storage format is invalid")
    envelope = SyncEventEnvelope.model_validate_json(
        canonical_json_bytes(raw["envelope"]), strict=True
    )
    return _anchored(envelope, anchor)
