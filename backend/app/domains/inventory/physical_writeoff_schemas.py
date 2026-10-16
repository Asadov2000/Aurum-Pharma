"""Warehouse write-off commands and cost-free recovery results."""

from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.domains.inventory.physical_schemas import IntegerString
from app.domains.inventory.schemas import WriteOffReason
from app.domains.inventory.unit_writeoff_v2 import PhysicalWriteOffSelectionV2


class PhysicalWriteOffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: UUID
    branch_id: UUID
    lot_id: UUID
    reason: WriteOffReason
    comment: str | None = Field(default=None, strict=True, max_length=2000)
    selection: PhysicalWriteOffSelectionV2

    @field_validator("comment")
    @classmethod
    def valid_comment(cls, value: str | None) -> str | None:
        if value is not None:
            if "\x00" in value:
                raise ValueError("Comment must not contain NUL")
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                raise ValueError("Comment must be valid UTF-8 text") from None
        return value


class PhysicalWriteOffRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: UUID
    branch_id: UUID
    lot_id: UUID
    reason: WriteOffReason
    comment: str | None
    base_units: IntegerString
    closed_packs: IntegerString
    created_at: AwareDatetime
