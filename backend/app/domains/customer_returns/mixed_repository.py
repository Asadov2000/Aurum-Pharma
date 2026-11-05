"""Only the scoped, cost-free database projection is available to queue readers."""

from datetime import date
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.customer_returns.reasons import RefundReasonCode
from app.domains.inventory.unit_stock_v2 import UnitPackagingV2
from app.domains.pos.unit_contracts import LegacyQuantity


class MixedCustomerReturnRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: UUID
    line_id: UUID
    inventory_kind: Literal["legacy_pack", "physical_unit"]
    batch_id: UUID | None
    lot_id: UUID | None
    branch_id: UUID
    branch_name: str
    report_timezone: str
    parent_sale_id: UUID
    parent_receipt_number: str
    return_sale_id: UUID | None
    return_receipt_number: str | None
    catalog_id: UUID
    catalog_name: str = Field(min_length=1, max_length=500)
    legacy_quantity: LegacyQuantity | None
    base_units: int | None = Field(strict=True, gt=0)
    packaging: UnitPackagingV2 | None
    batch_number: str | None
    expires_at: date
    received_at: AwareDatetime
    refund_reason: RefundReasonCode | None
    refund_comment: str | None
    intake_kind: Literal["cash_refund", "zero_return"]

    @field_validator("packaging", mode="before")
    @classmethod
    def explicit_historical_packaging(cls, value: object) -> UnitPackagingV2 | None:
        if value is None:
            return None
        if not isinstance(value, dict) or "version" not in value:
            raise ValueError("Historical physical packaging must have an explicit version")
        return UnitPackagingV2.model_validate(value)

    @model_validator(mode="after")
    def historical_stock_kind(self) -> Self:
        if self.inventory_kind == "legacy_pack":
            if (
                self.batch_id is None
                or self.legacy_quantity is None
                or self.lot_id is not None
                or self.base_units is not None
                or self.packaging is not None
            ):
                raise ValueError("Legacy quarantine requires historical decimal packs")
        elif (
            self.lot_id is None
            or self.base_units is None
            or self.packaging is None
            or self.batch_id is not None
            or self.legacy_quantity is not None
        ):
            raise ValueError("Physical quarantine requires historical packaging and units")
        return self


PROJECTION = """
operation_id,line_id,inventory_kind,batch_id,lot_id,branch_id,branch_name,report_timezone,
parent_sale_id,parent_receipt_number,
return_sale_id,return_receipt_number,catalog_id,catalog_name,legacy_quantity,base_units,packaging,
batch_number,expires_at,received_at,refund_reason,refund_comment,intake_kind
"""


class MixedCustomerReturnsRepository:
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
    ) -> tuple[list[MixedCustomerReturnRow], int]:
        if allowed_branch_ids is not None and (
            not allowed_branch_ids
            or (branch_id is not None and branch_id not in allowed_branch_ids)
        ):
            return [], 0
        source = "FROM public.mixed_customer_return_queue(:tenant,:branch,:search)"
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
                "ORDER BY received_at DESC,operation_id,line_id,lot_id NULLS FIRST,batch_id "
                "LIMIT :limit OFFSET :offset"
            ),
            {**parameters, "limit": page_size, "offset": (page - 1) * page_size},
        )
        items = [MixedCustomerReturnRow.model_validate(dict(row)) for row in rows.mappings()]
        return items, total
