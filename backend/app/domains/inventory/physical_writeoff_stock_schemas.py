"""Cost-free, paginated physical identities available for explicit write-off."""

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domains.inventory.physical_schemas import IntegerString, PhysicalLotRead
from app.domains.inventory.unit_stock_v2 import OpenedBlisterV2


class PhysicalWriteOffPackState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intact_blisters: list[Annotated[int, Field(strict=True, ge=1)]]
    opened_blisters: list[OpenedBlisterV2]
    loose_units: int = Field(strict=True, ge=0)


class PhysicalWriteOffPackRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack_id: UUID
    remaining_units: IntegerString
    state: PhysicalWriteOffPackState


class PhysicalWriteOffStockRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lot: PhysicalLotRead
    opened_packs: list[PhysicalWriteOffPackRead]
    total: int
    page: int
    page_size: int
