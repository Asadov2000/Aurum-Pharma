"""Manual payment contracts. Money input is an exact decimal string."""

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


def _money(value: object) -> Decimal:
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,2})?", value
    ):
        raise ValueError("Сумма должна быть строкой с не более чем двумя знаками после запятой")
    return Decimal(value)


MoneyInput = Annotated[Decimal, BeforeValidator(_money)]
Note = Annotated[str, Field(max_length=1000)]


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PeriodCreate(Request):
    employee_id: UUID
    period_from: date
    period_to: date
    amount: MoneyInput
    note: Note = ""
    operation_id: UUID

    @model_validator(mode="after")
    def ordered_dates(self) -> "PeriodCreate":
        if self.period_to < self.period_from:
            raise ValueError("Конец периода не может быть раньше начала")
        return self


class PeriodUpdate(Request):
    amount: MoneyInput
    note: Note
    expected_row_version: int = Field(ge=1, strict=True)


class EntryCreate(Request):
    kind: Literal["payment", "advance"]
    amount: Annotated[MoneyInput, Field(gt=0)]
    paid_on: date
    note: Note = ""
    operation_id: UUID


class EntryReverse(Request):
    reason: str = Field(min_length=3, max_length=1000)
    operation_id: UUID


class EmployeeSearch(Request):
    q: str = Field(default="", max_length=100)
    page: int = Field(default=1, ge=1, le=100000)
    page_size: int = Field(default=25, ge=1, le=50)


class EmployeeItem(BaseModel):
    id: UUID
    full_name: str
    status: str


class PeriodItem(BaseModel):
    id: UUID
    employee_id: UUID
    period_from: date
    period_to: date
    amount: Decimal
    paid_amount: Decimal
    advance_amount: Decimal
    remaining_amount: Decimal
    currency: Literal["TJS"] = "TJS"
    note: str
    row_version: int
    created_at: datetime
    updated_at: datetime


class EntryItem(BaseModel):
    id: UUID
    kind: Literal["payment", "advance", "reversal"]
    amount: Decimal
    paid_on: date
    note: str
    is_reversed: bool
    reverses_entry_id: UUID | None
    created_at: datetime


class RevisionItem(BaseModel):
    id: UUID
    amount: Decimal
    note: str
    row_version: int
    created_at: datetime


Item = TypeVar("Item")


class Page(BaseModel, Generic[Item]):
    items: list[Item]
    total: int
    page: int
    page_size: int
    timezone: str
