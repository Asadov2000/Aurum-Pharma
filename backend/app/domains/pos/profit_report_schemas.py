"""Signed sales economics and separate warehouse losses, with explicit cost coverage."""

from datetime import date
from decimal import Decimal
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator


class ProfitReportRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    line_id: UUID | None
    branch_id: UUID
    branch_name: str
    name: str
    occurred_at: AwareDatetime
    source_kind: Literal["legacy", "physical", "mixed"]
    event_kind: Literal["sale", "refund", "zero_return", "writeoff"]
    revenue: Decimal
    cost: Decimal | None
    gross_profit: Decimal | None
    cost_status: Literal["known", "unknown", "hidden"]

    @model_validator(mode="after")
    def consistent_economics(self) -> Self:
        if (self.cost_status == "known") != (self.cost is not None):
            raise ValueError("Profit cost visibility is inconsistent")
        if self.event_kind == "writeoff":
            if self.revenue != 0 or self.gross_profit is not None:
                raise ValueError("Warehouse losses are separate from sales profit")
        elif self.cost is None:
            if self.gross_profit is not None:
                raise ValueError("Profit requires known cost")
        elif self.gross_profit != self.revenue - self.cost:
            raise ValueError("Profit must reconcile with revenue and cost")
        if self.event_kind in {"zero_return", "writeoff"} and self.revenue != 0:
            raise ValueError("Non-payment events must not change revenue")
        if self.event_kind == "sale" and self.revenue < 0:
            raise ValueError("Sale revenue must not be negative")
        if self.event_kind == "refund" and self.revenue > 0:
            raise ValueError("Refund revenue must not be positive")
        if self.cost is not None:
            if self.event_kind in {"sale", "writeoff"} and self.cost < 0:
                raise ValueError("Sale and write-off costs must not be negative")
            if self.event_kind in {"refund", "zero_return"} and self.cost > 0:
                raise ValueError("Returned costs must not be positive")
        return self


class ProfitReportSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[ProfitReportRow]
    total: int
    sales_revenue: Decimal
    refunds: Decimal
    net_revenue: Decimal
    net_sales_cost: Decimal | None
    known_net_sales_cost: Decimal | None
    gross_profit: Decimal | None
    known_gross_profit: Decimal | None
    writeoff_cost: Decimal | None
    known_writeoff_cost: Decimal | None
    cost_status: Literal["known", "partial", "hidden"]
    writeoff_cost_status: Literal["known", "partial", "hidden"]

    @model_validator(mode="after")
    def consistent_totals(self) -> Self:
        if self.net_revenue != self.sales_revenue - self.refunds:
            raise ValueError("Report revenue must reconcile")
        if (self.cost_status == "known") != (
            self.net_sales_cost is not None and self.gross_profit is not None
        ):
            raise ValueError("Complete profit requires all costs")
        if self.cost_status != "known" and (
            self.net_sales_cost is not None or self.gross_profit is not None
        ):
            raise ValueError("Partial or hidden costs must not produce full profit")
        if (
            self.gross_profit is not None
            and self.net_sales_cost is not None
            and self.gross_profit != self.net_revenue - self.net_sales_cost
        ):
            raise ValueError("Report profit must reconcile")
        if self.cost_status == "hidden" and (
            self.known_net_sales_cost is not None or self.known_gross_profit is not None
        ):
            raise ValueError("Hidden summaries must not reveal costs")
        if (self.writeoff_cost_status == "known") != (self.writeoff_cost is not None):
            raise ValueError("Complete warehouse valuation requires all costs")
        if self.writeoff_cost_status == "hidden" and self.known_writeoff_cost is not None:
            raise ValueError("Hidden warehouse summaries must not reveal costs")
        return self


class ProfitReportOverview(ProfitReportSnapshot):
    date_from: date
    date_to: date
    currency: Literal["TJS"] = "TJS"
    page: int
    page_size: int
