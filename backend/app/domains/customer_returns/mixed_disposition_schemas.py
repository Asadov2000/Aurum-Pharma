"""Cost-free acknowledgment of a persisted, immutable mixed quarantine decision."""

from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator, model_validator

from app.domains.customer_returns.mixed_disposition import (
    FrozenDispositionContract,
    MixedDispositionRequest,
    MixedDispositionSnapshot,
    _disposition_hash,
)
from app.domains.customer_returns.schemas import (
    CustomerReturnReasonCode,
    CustomerReturnResolutionType,
)


class ResolvedMixedDisposition(FrozenDispositionContract):
    version: Literal[1]
    document_kind: Literal["mixed_customer_return_disposition"]
    state: Literal["resolved"]
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
    actor_id: UUID
    resolved_at: AwareDatetime

    @field_validator("version", mode="before")
    @classmethod
    def strict_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("version must be an integer")
        return value

    @field_validator("comment")
    @classmethod
    def persisted_comment_is_normalized(cls, value: str | None) -> str | None:
        if value is not None and (not value or value != value.strip()):
            raise ValueError("Persisted comment must already be normalized")
        return value

    def request(self) -> MixedDispositionRequest:
        target = self.target.model_dump(exclude={"legacy_quantity", "base_units", "unit_label"})
        return MixedDispositionRequest.model_validate(
            {
                "decision_operation_id": self.decision_operation_id,
                "target": target,
                "disposition_type": self.disposition_type,
                "reason_code": self.reason_code,
                "comment": self.comment,
            }
        )

    @model_validator(mode="after")
    def canonical_content(self) -> Self:
        if _disposition_hash(self.request(), tenant_id=self.tenant_id) != self.request_hash:
            raise ValueError("Persisted decision hash does not match its canonical content")
        return self


class MixedDispositionHistory(FrozenDispositionContract):
    items: list[ResolvedMixedDisposition]
    total: int = Field(strict=True, ge=0)
    page: int = Field(strict=True, ge=1)
    page_size: int = Field(strict=True, ge=1, le=200)
