"""Unsigned historical refund evidence; hashes never authorize payout or restocking."""

from datetime import UTC, datetime, timedelta
from typing import Literal, Self

from pydantic import AwareDatetime, ConfigDict, field_validator, model_validator

from app.domains.pos.mixed_cash_refund_schemas import MixedCashRefundResultV4
from app.domains.pos.mixed_electronic_refund_posting_schemas import PostedMixedElectronicRefund
from app.domains.pos.mixed_return_journal_schemas import MixedReturnJournalResult
from app.domains.pos.physical_cash_refund_schemas import PhysicalCashRefundResultV3
from app.domains.pos.physical_return_journal_schemas import PhysicalReturnJournalResult
from app.domains.pos.retail_sale_events import RetailSaleStreamPosition
from app.domains.pos.unit_contracts import FrozenContract
from app.domains.pos.unit_electronic_refund_posting_schemas import PostedElectronicRefund
from app.domains.pos.unit_events import StreamInteger
from app.domains.sync.integrity import (
    canonical_json_bytes,
    canonical_json_hash,
    projection_stream_checksum,
    source_stream_checksum,
)
from app.domains.sync.schemas import SyncEventEnvelope

RetailRefundSourceKind = Literal[
    "physical_cash", "mixed_cash", "physical_electronic", "mixed_electronic"
]
RetailRefundResult = (
    PhysicalCashRefundResultV3
    | MixedCashRefundResultV4
    | PostedElectronicRefund
    | PostedMixedElectronicRefund
)
RetailRefundQuarantine = PhysicalReturnJournalResult | MixedReturnJournalResult
MAX_REFUND_EVENT_BYTES = 1024 * 1024


def _physical_lines(
    result: PhysicalCashRefundResultV3 | PostedElectronicRefund,
    quarantine: PhysicalReturnJournalResult,
) -> None:
    if result.total_cost_amount != quarantine.total_cost_amount:
        raise ValueError("Refund historical costs differ")
    for line, received in zip(result.receipt.lines, quarantine.lines, strict=True):
        if (
            line.original_line_id,
            line.base_units,
            line.offset,
            line.gross_amount,
            line.discount_amount,
            line.net_amount,
        ) != (
            received.line_id,
            received.base_units,
            received.offset,
            received.gross_amount,
            received.discount_amount,
            received.net_amount,
        ):
            raise ValueError("Refund physical allocation differs")


def _mixed_lines(
    result: MixedCashRefundResultV4 | PostedMixedElectronicRefund,
    quarantine: MixedReturnJournalResult,
) -> None:
    if result.physical_cost_amount != quarantine.physical_cost_amount:
        raise ValueError("Refund historical costs differ")
    for line, received in zip(result.receipt.lines, quarantine.lines, strict=True):
        if (
            line.inventory_kind,
            line.original_line_id,
            line.original_position,
            line.quantity,
            line.offset,
            line.gross_amount,
            line.discount_amount,
            line.net_amount,
        ) != (
            received.inventory_kind,
            received.line_id,
            received.position,
            received.quantity,
            received.offset,
            received.gross_amount,
            received.discount_amount,
            received.net_amount,
        ):
            raise ValueError("Refund mixed allocation differs")
        if line.inventory_kind == "physical_unit":
            if received.inventory_kind != "physical_unit" or (
                line.base_units,
                line.sale_format,
            ) != (received.base_units, received.sale_format):
                raise ValueError("Refund mixed physical quantity differs")


class RetailRefundEventResult(FrozenContract):
    """A typed immutable document and its corresponding quarantine receipt."""

    schema_version: Literal[6] = 6
    document_kind: Literal["retail_refund_event"] = "retail_refund_event"
    source_kind: RetailRefundSourceKind
    result: RetailRefundResult
    quarantine: RetailRefundQuarantine

    @model_validator(mode="after")
    def bound_history(self) -> Self:
        expected = {
            "physical_cash": PhysicalCashRefundResultV3,
            "mixed_cash": MixedCashRefundResultV4,
            "physical_electronic": PostedElectronicRefund,
            "mixed_electronic": PostedMixedElectronicRefund,
        }
        if not isinstance(self.result, expected[self.source_kind]):
            raise ValueError("Refund source kind differs from its document")
        result, quarantine = self.result, self.quarantine
        if (
            result.operation_id,
            result.parent_sale_id,
            result.tenant_id,
            result.branch_id,
            result.actor_id,
            result.total_amount,
        ) != (
            quarantine.operation_id,
            quarantine.parent_sale_id,
            quarantine.tenant_id,
            quarantine.branch_id,
            quarantine.actor_id,
            quarantine.total_amount,
        ) or result.sale_id == result.parent_sale_id:
            raise ValueError("Refund quarantine scope differs")
        if result.receipt.completed_at.utcoffset() != timedelta(0):
            raise ValueError("Refund historical timestamp must be UTC")
        if isinstance(result, PhysicalCashRefundResultV3 | PostedElectronicRefund):
            if not isinstance(quarantine, PhysicalReturnJournalResult):
                raise ValueError("Physical refund requires its physical quarantine")
            _physical_lines(result, quarantine)
            slices = [part for line in quarantine.lines for part in line.slices]
        else:
            if not isinstance(quarantine, MixedReturnJournalResult):
                raise ValueError("Mixed refund requires its mixed quarantine")
            _mixed_lines(result, quarantine)
            slices = [
                part
                for line in quarantine.lines
                if line.inventory_kind == "physical_unit"
                for part in line.slices
            ]
        if len({part.source_movement_id for part in slices}) != len(slices):
            raise ValueError("Refund source movements must belong to one line")
        return self


class _RetailRefundEnvelope(SyncEventEnvelope):
    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        strict=True,
    )

    aggregate_type: Literal["sale"] = "sale"
    event_type: Literal["pos.sale.refunded.v6"] = "pos.sale.refunded.v6"
    schema_version: Literal[6] = 6
    writer_epoch: StreamInteger
    sequence: StreamInteger
    occurred_at: AwareDatetime

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("Refund event version must be an integer")
        return value

    @field_validator("occurred_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


def _bounded(event: SyncEventEnvelope) -> None:
    if len(canonical_json_bytes(event.model_dump(mode="json"))) > MAX_REFUND_EVENT_BYTES:
        raise ValueError("Refund event exceeds the size limit")


def build_retail_refund_event(
    result: RetailRefundEventResult, *, position: RetailSaleStreamPosition
) -> SyncEventEnvelope:
    """Build evidence without writing, reserving a stream or executing any refund."""
    result = RetailRefundEventResult.model_validate(result.model_dump(mode="python"))
    position = RetailSaleStreamPosition.model_validate(position.model_dump(mode="python"))
    source = result.result
    payload = result.model_dump(mode="json")
    digest = canonical_json_hash(payload)
    event = _RetailRefundEnvelope(
        event_id=source.operation_id,
        tenant_id=source.tenant_id,
        branch_id=source.branch_id,
        origin_node_id=position.origin_node_id,
        writer_epoch=position.writer_epoch,
        sequence=position.sequence,
        operation_id=source.operation_id,
        aggregate_id=source.sale_id,
        occurred_at=source.receipt.completed_at,
        payload=payload,
        payload_hash=digest,
        projection_hash=digest,
        stream_checksum=source_stream_checksum(
            previous_checksum=position.previous_source_checksum,
            event_id=source.operation_id,
            tenant_id=source.tenant_id,
            branch_id=source.branch_id,
            origin_node_id=position.origin_node_id,
            writer_epoch=position.writer_epoch,
            sequence=position.sequence,
            operation_id=source.operation_id,
            aggregate_type="sale",
            aggregate_id=source.sale_id,
            event_type="pos.sale.refunded.v6",
            schema_version=6,
            occurred_at=source.receipt.completed_at,
            payload_hash=digest,
        ),
        projection_checksum=projection_stream_checksum(
            previous_checksum=position.previous_projection_checksum,
            origin_node_id=position.origin_node_id,
            writer_epoch=position.writer_epoch,
            sequence=position.sequence,
            sale_id=source.sale_id,
            projection_hash=digest,
        ),
    )
    _bounded(event)
    return event


def _checked_payload(
    envelope: SyncEventEnvelope,
) -> tuple[_RetailRefundEnvelope, RetailRefundEventResult]:
    event = _RetailRefundEnvelope.model_validate(envelope.model_dump(mode="python"))
    _bounded(event)
    result = RetailRefundEventResult.model_validate(event.payload)
    if canonical_json_bytes(result.model_dump(mode="json")) != canonical_json_bytes(event.payload):
        raise ValueError("Refund payload must use canonical historical serialization")
    source = result.result
    if (
        event.event_id,
        event.operation_id,
        event.aggregate_id,
        event.tenant_id,
        event.branch_id,
        event.occurred_at,
    ) != (
        source.operation_id,
        source.operation_id,
        source.sale_id,
        source.tenant_id,
        source.branch_id,
        source.receipt.completed_at,
    ):
        raise ValueError("Refund envelope scope differs from its document")
    digest = canonical_json_hash(event.payload)
    if event.payload_hash != digest or event.projection_hash != digest:
        raise ValueError("Refund payload or projection hash differs")
    return event, result


def validate_retail_refund_event(
    envelope: SyncEventEnvelope,
    *,
    previous_source_checksum: str,
    previous_projection_checksum: str,
) -> RetailRefundEventResult:
    """Check unsigned integrity only; a separate protected anchor establishes trust."""
    event, result = _checked_payload(envelope)
    expected = build_retail_refund_event(
        result,
        position=RetailSaleStreamPosition(
            origin_node_id=event.origin_node_id,
            writer_epoch=event.writer_epoch,
            sequence=event.sequence,
            previous_source_checksum=previous_source_checksum,
            previous_projection_checksum=previous_projection_checksum,
        ),
    )
    if event.stream_checksum != expected.stream_checksum:
        raise ValueError("Refund source checksum differs")
    if event.projection_checksum != expected.projection_checksum:
        raise ValueError("Refund projection checksum differs")
    return result


def retail_refund_event_fingerprint(envelope: SyncEventEnvelope) -> str:
    """A replay identity, never proof that an unsigned document is authentic."""
    event, _ = _checked_payload(envelope)
    return canonical_json_hash(
        {
            "domain": "aurum-retail-refund-event",
            "schema_version": 6,
            "envelope": event.model_dump(mode="json"),
        }
    )
