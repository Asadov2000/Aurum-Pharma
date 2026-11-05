"""One scoped snapshot for stock rows, pagination and separate quantity totals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import local_day_range


@dataclass(frozen=True, slots=True)
class StockQueryRow:
    stock_id: UUID
    inventory_kind: str
    name: str
    inn: str | None
    branch_name: str | None
    batch_number: str | None
    expires_at: date | None
    qty: Decimal
    physical_stock: object
    purchase_price: Decimal | None
    value: Decimal | None
    cost_status: str
    currency: str


@dataclass(frozen=True, slots=True)
class StockQuerySnapshot:
    rows: list[StockQueryRow]
    total: int
    total_qty: Decimal
    total_value: Decimal | None
    known_total_value: Decimal | None
    cost_status: str
    physical_quantities: object


async def read_stock_snapshot(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    on_date: date,
    branch_id: UUID | None,
    tz: str,
    query: str | None,
    expires_before: date | None,
    page: int | None = None,
    page_size: int | None = None,
) -> StockQuerySnapshot:
    _, cutoff = local_day_range(on_date, tz)
    clauses = []
    params: dict[str, object] = {"tenant": tenant_id, "cutoff": cutoff, "branch": branch_id}
    if query and query.strip():
        clauses.append("(name ILIKE :search OR inn ILIKE :search OR batch_number ILIKE :search)")
        params["search"] = f"%{query.strip()}%"
    if expires_before is not None:
        clauses.append("expires_at<=:expires_before")
        params["expires_before"] = expires_before
    where = " AND ".join(clauses) or "true"
    pagination = ""
    if page is not None:
        if page < 1 or page_size is None or not 1 <= page_size <= 100:
            raise ValueError("valid stock report pagination is required")
        pagination = "LIMIT :page_size OFFSET :offset"
        params.update(page_size=page_size, offset=(page - 1) * page_size)
    sql = text(f"""
        WITH filtered AS MATERIALIZED (
          SELECT * FROM public.pos_stock_on_date_for_report(:tenant,:cutoff,:branch)
          WHERE {where}
        ), totals AS (
          SELECT count(*) AS total,coalesce(sum(qty),0) AS total_qty,
            CASE WHEN coalesce(bool_and(cost_status='known'),true)
              THEN coalesce(sum(value),0) ELSE NULL END AS total_value,
            CASE WHEN coalesce(bool_and(cost_status='hidden'),false) THEN NULL
              ELSE coalesce(sum(value) FILTER(WHERE cost_status='known'),0)
              END AS known_total_value,
            CASE WHEN coalesce(bool_and(cost_status='known'),true) THEN 'known'
              WHEN bool_and(cost_status='hidden') THEN 'hidden' ELSE 'partial'
              END AS report_cost_status
          FROM filtered
        ), unit_totals AS (
          SELECT physical_stock->'packaging'->>'unit' AS unit,
            sum((physical_stock->>'base_units')::NUMERIC) AS quantity
          FROM filtered WHERE inventory_kind='physical'
          GROUP BY physical_stock->'packaging'->>'unit'
        ), unit_summary AS (
          SELECT coalesce(jsonb_agg(jsonb_build_object('unit',unit,'quantity',quantity::TEXT)
            ORDER BY unit),'[]'::JSONB) AS physical_quantities FROM unit_totals
        ), page_rows AS (
          SELECT * FROM filtered
          ORDER BY name NULLS LAST,expires_at NULLS LAST,stock_id,inventory_kind {pagination}
        )
        SELECT p.*,t.total,t.total_qty,t.total_value,t.known_total_value,t.report_cost_status,
          u.physical_quantities
        FROM totals t CROSS JOIN unit_summary u LEFT JOIN page_rows p ON true
        ORDER BY p.name NULLS LAST,p.expires_at NULLS LAST,p.stock_id,p.inventory_kind
    """).columns(physical_stock=JSONB(), physical_quantities=JSONB())
    records = (await session.execute(sql, params)).mappings().all()
    summary = records[0]
    rows = [
        StockQueryRow(
            stock_id=UUID(str(row["stock_id"])),
            inventory_kind=str(row["inventory_kind"]),
            name=str(row["name"] or "—"),
            inn=cast(str | None, row["inn"]),
            branch_name=cast(str | None, row["branch_name"]),
            batch_number=cast(str | None, row["batch_number"]),
            expires_at=cast(date | None, row["expires_at"]),
            qty=Decimal(str(row["qty"])),
            physical_stock=row["physical_stock"],
            purchase_price=(
                Decimal(str(row["purchase_price"])) if row["purchase_price"] is not None else None
            ),
            value=Decimal(str(row["value"])) if row["value"] is not None else None,
            cost_status=str(row["cost_status"]),
            currency=str(row["currency"] or "TJS"),
        )
        for row in records
        if row["stock_id"] is not None
    ]
    return StockQuerySnapshot(
        rows=rows,
        total=int(summary["total"]),
        total_qty=Decimal(str(summary["total_qty"])),
        total_value=(
            Decimal(str(summary["total_value"])) if summary["total_value"] is not None else None
        ),
        known_total_value=(
            Decimal(str(summary["known_total_value"]))
            if summary["known_total_value"] is not None
            else None
        ),
        cost_status=str(summary["report_cost_status"]),
        physical_quantities=summary["physical_quantities"],
    )
