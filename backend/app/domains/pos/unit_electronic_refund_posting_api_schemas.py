"""Public posting details; allocations and terminal evidence stay server-owned."""

from uuid import UUID

from pydantic import Field

from app.domains.customer_returns.reasons import RefundReasonCode
from app.domains.pos.unit_contracts import FrozenContract


class PostElectronicRefundRequest(FrozenContract):
    shift_id: UUID
    reason_code: RefundReasonCode | None = None
    comment: str | None = Field(default=None, max_length=500, strict=True)
