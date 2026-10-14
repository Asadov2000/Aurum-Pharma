"""Cost-free physical inventory contracts with exact integer quantities."""

from datetime import date
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app.domains.inventory.unit_stock_v2 import UnitPackagingV2

IntegerString = Annotated[str, Field(strict=True, pattern=r"^(0|[1-9][0-9]*)$", max_length=30)]


class PhysicalLotRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lot_id: UUID
    branch_id: UUID
    catalog_id: UUID
    catalog_name: str | None
    expires_at: date
    packaging: UnitPackagingV2
    closed_packs: IntegerString
    opened_units: IntegerString
    base_units: IntegerString
    is_blocked: bool
    blocking_revision: IntegerString


class PhysicalLotList(BaseModel):
    items: list[PhysicalLotRead]
    total: int
    page: int
    page_size: int


class PhysicalLotBlockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    branch_id: UUID
    is_blocked: StrictBool
    expected_blocking_revision: IntegerString


class PhysicalLotBlockRead(BaseModel):
    lot_id: UUID
    branch_id: UUID
    is_blocked: bool
    blocking_revision: IntegerString
