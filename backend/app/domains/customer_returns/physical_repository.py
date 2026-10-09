"""Only the scoped, cost-free database projection is available to queue readers."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.customer_returns.reasons import RefundReasonCode
from app.domains.inventory.unit_stock_v2 import UnitPackagingV2


class PhysicalCustomerReturnRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: UUID
    line_id: UUID
    lot_id: UUID
    branch_id: UUID
    branch_name: str
    report_timezone: str
    parent_sale_id: UUID
    parent_receipt_number: str
    return_sale_id: UUID | None
    return_receipt_number: str | None
    catalog_id: UUID
    catalog_name: str = Field(min_length=1, max_length=500)
    base_units: int = Field(strict=True, gt=0)
    packaging: UnitPackagingV2
    batch_number: str | None
    expires_at: date
    received_at: AwareDatetime
    refund_reason: RefundReasonCode | None
    refund_comment: str | None
    intake_kind: Literal["cash_refund", "zero_return"]

    @field_validator("packaging", mode="before")
    @classmethod
    def explicit_historical_packaging(cls, value: object) -> UnitPackagingV2:
        if not isinstance(value, dict) or "version" not in value:
            raise ValueError("Historical physical packaging must have an explicit version")
        return UnitPackagingV2.model_validate(value)


PROJECTION = """
operation_id,line_id,lot_id,branch_id,branch_name,report_timezone,
parent_sale_id,parent_receipt_number,
return_sale_id,return_receipt_number,catalog_id,catalog_name,base_units,packaging,
batch_number,expires_at,received_at,refund_reason,refund_comment,intake_kind
"""


class PhysicalCustomerReturnsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_items(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID | None,
        search: str | None,
        allowed_branch_ids: set[UUID] | None,
        page: int,
        page_size: int,
    ) -> tuple[list[PhysicalCustomerReturnRow], int]:
        if allowed_branch_ids is not None and (
            not allowed_branch_ids
            or (branch_id is not None and branch_id not in allowed_branch_ids)
        ):
            return [], 0
        source = "FROM public.physical_customer_return_queue(:tenant,:branch,:search)"
        parameters: dict[str, object] = {
            "tenant": tenant_id,
            "branch": branch_id,
            "search": search.strip() or None if search is not None else None,
        }
        if allowed_branch_ids is not None:
            source += " WHERE branch_id=ANY(CAST(:allowed AS UUID[]))"
            parameters["allowed"] = sorted(allowed_branch_ids, key=str)
        total = int(await self.session.scalar(text(f"SELECT count(*) {source}"), parameters) or 0)
        rows = await self.session.execute(
            text(
                f"SELECT {PROJECTION} {source} "
                "ORDER BY received_at DESC,operation_id,line_id,lot_id LIMIT :limit OFFSET :offset"
            ),
            {**parameters, "limit": page_size, "offset": (page - 1) * page_size},
        )
        items = [PhysicalCustomerReturnRow.model_validate(dict(row)) for row in rows.mappings()]
        return items, total
