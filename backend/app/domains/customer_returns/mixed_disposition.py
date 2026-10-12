"""Pure preparation of a whole confirmed mixed quarantine row's disposition.

This contract neither authorizes nor records an action. A future writer must fetch
the source through a tenant-bound, resolve-authorized database path, lock it, and
recheck its immutable snapshot before persisting a decision. A browser-supplied
queue DTO is not such a source; tenant_id here scopes the hash, not authorization.
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.errors import ConflictError
from app.domains.customer_returns.mixed_schemas import MixedCustomerReturnRead
from app.domains.customer_returns.schemas import (
    CustomerReturnReasonCode,
    CustomerReturnResolutionType,
)
from app.domains.pos.unit_contracts import LegacyQuantity
from app.domains.pos.unit_documents import UnitLabel


class FrozenDispositionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")


class MixedLegacyDispositionTarget(FrozenDispositionContract):
    inventory_kind: Literal["legacy_pack"]
    intake_operation_id: UUID
    line_id: UUID
    batch_id: UUID


class MixedPhysicalDispositionTarget(FrozenDispositionContract):
    inventory_kind: Literal["physical_unit"]
    intake_operation_id: UUID
    line_id: UUID
    lot_id: UUID


MixedDispositionTarget = Annotated[
    MixedLegacyDispositionTarget | MixedPhysicalDispositionTarget,
    Field(discriminator="inventory_kind"),
]


class MixedDispositionRequest(FrozenDispositionContract):
    decision_operation_id: UUID
    target: MixedDispositionTarget
    disposition_type: CustomerReturnResolutionType
    reason_code: CustomerReturnReasonCode
    comment: str | None = Field(default=None, strict=True, max_length=2000)

    @field_validator("comment", mode="before")
    @classmethod
    def normalize_comment(cls, value: object) -> object:
        return value.strip() or None if isinstance(value, str) else value

    @model_validator(mode="after")
    def separate_decision_operation(self) -> Self:
        if self.decision_operation_id == self.target.intake_operation_id:
            raise ValueError("Disposition and intake must use separate operation identities")
        return self


class MixedLegacyDispositionSnapshot(MixedLegacyDispositionTarget):
    legacy_quantity: LegacyQuantity
    unit_label: Literal["уп."]


class MixedPhysicalDispositionSnapshot(MixedPhysicalDispositionTarget):
    base_units: int = Field(strict=True, gt=0)
    unit_label: UnitLabel

    @field_validator("unit_label")
    @classmethod
    def historical_base_unit(cls, value: UnitLabel) -> UnitLabel:
        if value == "уп.":
            raise ValueError("Physical disposition requires a historical base-unit label")
        return value


MixedDispositionSnapshot = Annotated[
    MixedLegacyDispositionSnapshot | MixedPhysicalDispositionSnapshot,
    Field(discriminator="inventory_kind"),
]


class PreparedMixedDisposition(FrozenDispositionContract):
    """A validated proposal, with no claim that disposition has occurred."""

    version: Literal[1] = 1
    document_kind: Literal["mixed_customer_return_disposition"] = (
        "mixed_customer_return_disposition"
    )
    state: Literal["prepared"] = "prepared"
    decision_operation_id: UUID
    tenant_id: UUID
    branch_id: UUID
    parent_sale_id: UUID
    intake_kind: Literal["cash_refund", "zero_return"]
    target: MixedDispositionSnapshot
    disposition_type: CustomerReturnResolutionType
    reason_code: CustomerReturnReasonCode
    comment: str | None = Field(strict=True, max_length=2000)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("version", mode="before")
    @classmethod
    def strict_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("version must be an integer")
        return value


def _disposition_hash(request: MixedDispositionRequest, *, tenant_id: UUID) -> str:
    # Immutable quantities and labels belong to the trusted snapshot, not the
    # client payload. The decision operation is a separate idempotency key.
    payload = {
        "version": 1,
        "domain": "mixed_customer_return_disposition",
        "tenant_id": str(tenant_id),
        "target": request.target.model_dump(mode="json"),
        "disposition_type": request.disposition_type,
        "reason_code": request.reason_code,
        "comment_hash": (
            sha256(request.comment.encode("utf-8")).hexdigest()
            if request.comment is not None
            else None
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def prepare_mixed_disposition(
    request: MixedDispositionRequest,
    source: MixedCustomerReturnRead,
    *,
    tenant_id: UUID,
) -> PreparedMixedDisposition:
    """Bind a decision to an exact trusted whole row, without I/O or permission checks."""
    # Frozen instances can still be made invalid via model_copy/model_construct.
    request = MixedDispositionRequest.model_validate(request.model_dump())
    source = MixedCustomerReturnRead.model_validate(source.model_dump())
    target = request.target
    source_identity = (source.operation_id, source.line_id, source.inventory_kind)
    target_identity = (target.intake_operation_id, target.line_id, target.inventory_kind)
    if source_identity != target_identity:
        raise ConflictError("Mixed quarantine target does not match the confirmed source")
    snapshot: MixedLegacyDispositionSnapshot | MixedPhysicalDispositionSnapshot
    if isinstance(target, MixedLegacyDispositionTarget):
        if target.batch_id != source.batch_id or source.legacy_quantity is None:
            raise ConflictError("Mixed quarantine batch does not match the confirmed source")
        snapshot = MixedLegacyDispositionSnapshot(
            inventory_kind="legacy_pack",
            intake_operation_id=source.operation_id,
            line_id=source.line_id,
            batch_id=target.batch_id,
            legacy_quantity=source.legacy_quantity,
            unit_label="уп.",
        )
    else:
        if target.lot_id != source.lot_id or source.base_units is None:
            raise ConflictError("Mixed quarantine lot does not match the confirmed source")
        snapshot = MixedPhysicalDispositionSnapshot(
            inventory_kind="physical_unit",
            intake_operation_id=source.operation_id,
            line_id=source.line_id,
            lot_id=target.lot_id,
            base_units=source.base_units,
            unit_label=source.unit_label,
        )
    return PreparedMixedDisposition(
        decision_operation_id=request.decision_operation_id,
        tenant_id=tenant_id,
        branch_id=source.branch_id,
        parent_sale_id=source.parent_sale_id,
        intake_kind=source.intake_kind,
        target=snapshot,
        disposition_type=request.disposition_type,
        reason_code=request.reason_code,
        comment=request.comment,
        request_hash=_disposition_hash(request, tenant_id=tenant_id),
    )
