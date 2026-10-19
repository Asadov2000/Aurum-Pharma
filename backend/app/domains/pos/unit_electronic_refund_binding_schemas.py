"""Recoverable exclusive preparation, without authority to issue a refund."""

from typing import Literal, Self
from uuid import UUID

from pydantic import UUID4, AwareDatetime, Field, model_validator

from app.domains.pos.unit_contracts import FrozenContract


class UnitElectronicRefundBindingRead(FrozenContract):
    state: Literal["bound", "cancelled"]
    operation_id: UUID4
    parent_sale_id: UUID
    review_revision_hash: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")
    bound_at: AwareDatetime | None
    cancelled_at: AwareDatetime | None

    @model_validator(mode="after")
    def coherent_state(self) -> Self:
        if self.state == "bound":
            if self.bound_at is None or self.cancelled_at is not None:
                raise ValueError("Bound preparation requires only a binding timestamp")
        elif self.cancelled_at is None:
            raise ValueError("Cancelled preparation requires a cancellation timestamp")
        if (
            self.bound_at is not None
            and self.cancelled_at is not None
            and self.cancelled_at < self.bound_at
        ):
            raise ValueError("Cancellation cannot precede an existing binding")
        return self
