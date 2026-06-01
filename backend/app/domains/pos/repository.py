"""DB access for the POS domain."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, distinct, func, literal, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory.models import Batch
from app.domains.pos.models import (
    PrescriptionLog,
    Sale,
    SaleItem,
    SalePayment,
    Shift,
)


class POSRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -------- shift --------

    async def get_open_shift_for_register(self, register_id: UUID) -> Shift | None:
        stmt = (
            select(Shift)
            .where(and_(Shift.register_id == register_id, Shift.status == "open"))
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_open_shift_for_user(self, user_id: UUID, register_id: UUID) -> Shift | None:
        stmt = (
            select(Shift)
            .where(
                and_(
                    Shift.opened_by_user_id == user_id,
                    Shift.register_id == register_id,
                    Shift.status == "open",
                )
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_shift(self, **fields: Any) -> Shift:
        s = Shift(**fields)
        self.session.add(s)
        await self.session.flush()
        await self.session.refresh(s)
        return s

    async def get_shift(self, shift_id: UUID) -> Shift | None:
        return await self.session.get(Shift, shift_id)

    async def update_shift(self, shift: Shift, **fields: Any) -> Shift:
        for k, v in fields.items():
            setattr(shift, k, v)
        await self.session.flush()
        await self.session.refresh(shift)
        return shift

    # -------- sale --------

    async def create_sale(self, **fields: Any) -> Sale:
        s = Sale(**fields)
        self.session.add(s)
        await self.session.flush()
        await self.session.refresh(s)
        return s

    async def get_sale(self, sale_id: UUID) -> Sale | None:
        return await self.session.get(Sale, sale_id)

    async def update_sale(self, sale: Sale, **fields: Any) -> Sale:
        for k, v in fields.items():
            setattr(sale, k, v)
        await self.session.flush()
        await self.session.refresh(sale)
        return sale

    async def list_items(self, sale_id: UUID) -> list[SaleItem]:
        stmt = select(SaleItem).where(SaleItem.sale_id == sale_id).order_by(SaleItem.position.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_item(self, item_id: UUID) -> SaleItem | None:
        return await self.session.get(SaleItem, item_id)

    async def insert_item(self, **fields: Any) -> SaleItem:
        item = SaleItem(**fields)
        self.session.add(item)
        await self.session.flush()
        await self.session.refresh(item)
        return item

    async def update_item(self, item: SaleItem, **fields: Any) -> SaleItem:
        for k, v in fields.items():
            setattr(item, k, v)
        await self.session.flush()
        await self.session.refresh(item)
        return item

    async def delete_item(self, item_id: UUID, sale_id: UUID) -> int:
        from sqlalchemy import delete

        result = await self.session.execute(
            delete(SaleItem).where(and_(SaleItem.id == item_id, SaleItem.sale_id == sale_id))
        )
        return result.rowcount or 0  # type: ignore[attr-defined]

    async def next_item_position(self, sale_id: UUID) -> int:
        stmt = select(func.coalesce(func.max(SaleItem.position), 0)).where(
            SaleItem.sale_id == sale_id
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one()) + 1

    # -------- payments --------

    async def insert_payment(self, **fields: Any) -> SalePayment:
        p = SalePayment(**fields)
        self.session.add(p)
        await self.session.flush()
        await self.session.refresh(p)
        return p

    async def list_payments(self, sale_id: UUID) -> list[SalePayment]:
        stmt = select(SalePayment).where(SalePayment.sale_id == sale_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def payments_total(self, sale_id: UUID) -> Decimal:
        stmt = select(func.coalesce(func.sum(SalePayment.amount), 0)).where(
            SalePayment.sale_id == sale_id
        )
        result = await self.session.execute(stmt)
        return Decimal(str(result.scalar_one()))

    # -------- prescription_log --------

    async def insert_prescription(self, **fields: Any) -> PrescriptionLog:
        pl = PrescriptionLog(**fields)
        self.session.add(pl)
        await self.session.flush()
        await self.session.refresh(pl)
        return pl

    async def list_prescriptions(self, sale_id: UUID) -> list[PrescriptionLog]:
        stmt = select(PrescriptionLog).where(PrescriptionLog.sale_id == sale_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # -------- receipt / aggregations --------

    async def next_receipt_number(self, shift_id: UUID) -> str:
        """Sequential per shift: count of completed sales in this shift + 1.
        Padded to 6 digits so the string sorts."""
        stmt = (
            select(func.count())
            .select_from(Sale)
            .where(
                and_(
                    Sale.shift_id == shift_id,
                    Sale.status == "completed",
                    Sale.receipt_number.is_not(None),
                )
            )
        )
        result = await self.session.execute(stmt)
        n = int(result.scalar_one()) + 1
        return f"{n:06d}"

    async def shift_totals(self, shift_id: UUID) -> dict[str, Any]:
        """Aggregates payments grouped by method for sales in this shift,
        plus sales_count / returns_count."""
        payment_sum_stmt = (
            select(SalePayment.payment_method, func.coalesce(func.sum(SalePayment.amount), 0))
            .join(Sale, Sale.id == SalePayment.sale_id)
            .where(and_(Sale.shift_id == shift_id, Sale.status == "completed"))
            .group_by(SalePayment.payment_method)
        )
        result = await self.session.execute(payment_sum_stmt)
        totals: dict[str, Any] = {
            "cash": "0",
            "card": "0",
            "bank_transfer": "0",
        }
        for method, total in result.all():
            totals[method] = str(total)

        sales_count_stmt = (
            select(func.count())
            .select_from(Sale)
            .where(
                and_(
                    Sale.shift_id == shift_id,
                    Sale.status == "completed",
                    Sale.sale_type == "sale",
                )
            )
        )
        returns_count_stmt = (
            select(func.count())
            .select_from(Sale)
            .where(
                and_(
                    Sale.shift_id == shift_id,
                    Sale.status == "completed",
                    Sale.sale_type == "return",
                )
            )
        )
        sales_count = int((await self.session.execute(sales_count_stmt)).scalar_one())
        returns_count = int((await self.session.execute(returns_count_stmt)).scalar_one())
        totals["sales_count"] = sales_count
        totals["returns_count"] = returns_count
        return totals

    async def z_report_aggregates(self, shift_id: UUID) -> dict[str, Any]:
        """Richer aggregation for the Z-report XLSX: gross sales + count,
        discounts, refunds + count, and a payment breakdown where each sale is
        bucketed by its method (≥2 distinct methods → 'mixed')."""

        def _sale_filter(sale_type: str) -> Any:
            return and_(
                Sale.shift_id == shift_id,
                Sale.status == "completed",
                Sale.sale_type == sale_type,
            )

        sales_stmt = select(func.coalesce(func.sum(Sale.total_amount), 0), func.count()).where(
            _sale_filter("sale")
        )
        total_sales, sales_count = (await self.session.execute(sales_stmt)).one()

        refunds_stmt = select(func.coalesce(func.sum(Sale.total_amount), 0), func.count()).where(
            _sale_filter("return")
        )
        total_refunds, returns_count = (await self.session.execute(refunds_stmt)).one()

        disc_stmt = (
            select(func.coalesce(func.sum(SaleItem.discount_amount), 0))
            .select_from(SaleItem)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(_sale_filter("sale"))
        )
        total_discounts = (await self.session.execute(disc_stmt)).scalar_one()

        # Per-sale method: count distinct methods on its payment lines; >1 → mixed.
        per_sale = (
            select(
                Sale.total_amount.label("amt"),
                func.count(distinct(SalePayment.payment_method)).label("n_methods"),
                func.min(SalePayment.payment_method).label("only_method"),
            )
            .select_from(Sale)
            .join(SalePayment, SalePayment.sale_id == Sale.id, isouter=True)
            .where(_sale_filter("sale"))
            .group_by(Sale.id, Sale.total_amount)
            .subquery()
        )
        method = case((per_sale.c.n_methods > 1, literal("mixed")), else_=per_sale.c.only_method)
        breakdown_stmt = select(
            method.label("method"), func.coalesce(func.sum(per_sale.c.amt), 0)
        ).group_by(method)

        breakdown: dict[str, Decimal] = {
            "cash": Decimal("0"),
            "card": Decimal("0"),
            "bank_transfer": Decimal("0"),
            "mixed": Decimal("0"),
        }
        for m, total in (await self.session.execute(breakdown_stmt)).all():
            if m in breakdown:
                breakdown[m] = Decimal(str(total))

        return {
            "total_sales": Decimal(str(total_sales)),
            "sales_count": int(sales_count),
            "total_discounts": Decimal(str(total_discounts)),
            "total_refunds": Decimal(str(total_refunds)),
            "returns_count": int(returns_count),
            "payment_breakdown": breakdown,
        }

    # -------- batch lock (concurrent-safe complete) --------

    async def lock_batch(self, batch_id: UUID) -> Batch | None:
        """SELECT ... FOR UPDATE the batch row so a concurrent complete can't
        race us into a negative qty_remaining. Returns the fresh row."""
        stmt = select(Batch).where(Batch.id == batch_id).with_for_update()
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # -------- sales listing (receipt search) --------

    async def list_sales(
        self,
        *,
        tenant_id: UUID,
        cashier_id: UUID | None,
        branch_id: UUID | None,
        register_id: UUID | None,
        receipt_number: str | None,
        date_from: Any | None,
        date_to: Any | None,
        has_refund: bool | None,
        min_total: Decimal | None,
        max_total: Decimal | None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """One query joins sale → branch/register/cashier for resolved names,
        aggregates payment methods, builds a short item preview, and derives
        is_refund / has_refund / refund_receipt_number from the parent↔child
        sale link. Only receipts (completed/voided, completed_at set) appear —
        drafts have no receipt_number.

        `has_refund` is true when at least one completed return-sale points at
        this row; `refund_receipt_number` is that return's receipt. Deriving
        these avoids a denormalized column that could drift.
        """
        clauses = ["s.tenant_id = :tid", "s.completed_at IS NOT NULL"]
        params: dict[str, Any] = {"tid": str(tenant_id)}

        if cashier_id is not None:
            clauses.append("s.cashier_user_id = :cashier")
            params["cashier"] = str(cashier_id)
        if branch_id is not None:
            clauses.append("s.branch_id = :branch")
            params["branch"] = str(branch_id)
        if register_id is not None:
            clauses.append("s.register_id = :register")
            params["register"] = str(register_id)
        if receipt_number is not None:
            clauses.append("s.receipt_number = :receipt")
            params["receipt"] = receipt_number
        if date_from is not None:
            clauses.append("s.completed_at::date >= :dfrom")
            params["dfrom"] = date_from
        if date_to is not None:
            clauses.append("s.completed_at::date <= :dto")
            params["dto"] = date_to
        if min_total is not None:
            clauses.append("s.total_amount >= :mintot")
            params["mintot"] = min_total
        if max_total is not None:
            clauses.append("s.total_amount <= :maxtot")
            params["maxtot"] = max_total
        # has_refund filter operates on the derived EXISTS.
        refund_exists = (
            "EXISTS (SELECT 1 FROM sale r WHERE r.parent_sale_id = s.id "
            "AND r.sale_type = 'return' AND r.status = 'completed')"
        )
        if has_refund is True:
            clauses.append(refund_exists)
        elif has_refund is False:
            clauses.append(f"NOT {refund_exists}")

        where = " AND ".join(clauses)

        count_sql = text(f"SELECT COUNT(*) FROM sale s WHERE {where}")
        total = int((await self.session.execute(count_sql, params)).scalar_one())

        params["limit"] = page_size
        params["offset"] = (page - 1) * page_size
        rows_sql = text(
            f"""
            SELECT
              s.id, s.receipt_number, s.completed_at, s.status,
              s.total_amount, s.currency, s.parent_sale_id,
              s.sale_type = 'return' AS is_refund,
              b.name  AS branch_name,
              reg.name AS register_name,
              u.full_name AS cashier_name,
              parent.receipt_number AS parent_receipt_number,
              {refund_exists} AS has_refund,
              (SELECT r2.receipt_number FROM sale r2
                 WHERE r2.parent_sale_id = s.id AND r2.sale_type = 'return'
                 AND r2.status = 'completed'
                 ORDER BY r2.completed_at DESC LIMIT 1) AS refund_receipt_number,
              COALESCE(
                (SELECT array_agg(DISTINCT p.payment_method)
                   FROM sale_payment p WHERE p.sale_id = s.id),
                ARRAY[]::text[]
              ) AS payment_methods,
              COALESCE(
                (SELECT string_agg(line, ', ')
                   FROM (
                     SELECT tc.brand_name || ' x' || (si.qty)::float8::text AS line
                       FROM sale_item si
                       JOIN tenant_catalog tc ON tc.id = si.catalog_id
                      WHERE si.sale_id = s.id
                      ORDER BY si.position
                      LIMIT 5
                   ) z),
                ''
              ) AS items_summary
            FROM sale s
            LEFT JOIN branch b   ON b.id = s.branch_id
            LEFT JOIN register reg ON reg.id = s.register_id
            LEFT JOIN app_user u ON u.id = s.cashier_user_id
            LEFT JOIN sale parent ON parent.id = s.parent_sale_id
            WHERE {where}
            ORDER BY s.completed_at DESC
            LIMIT :limit OFFSET :offset
            """
        )
        rows = (await self.session.execute(rows_sql, params)).mappings().all()
        return [dict(r) for r in rows], total

    async def sales_summary(
        self,
        *,
        tenant_id: UUID,
        date_from: Any,
        date_to: Any,
        branch_id: UUID | None,
    ) -> dict[str, Any]:
        """Detail rows + period totals for the accountant summary over
        [date_from, date_to]. Totals use the same status='completed' basis as
        the Z-report; the payment breakdown buckets each forward sale by its
        method (≥2 distinct → 'mixed'), identical to z_report_aggregates, so a
        single shift's range reconciles with its Z-report.
        """
        base = ["s.tenant_id = :tid", "s.completed_at IS NOT NULL"]
        params: dict[str, Any] = {"tid": str(tenant_id), "dfrom": date_from, "dto": date_to}
        base.append("s.completed_at::date >= :dfrom")
        base.append("s.completed_at::date <= :dto")
        if branch_id is not None:
            base.append("s.branch_id = :branch")
            params["branch"] = str(branch_id)
        where = " AND ".join(base)
        fwd_done = f"{where} AND s.sale_type = 'sale' AND s.status = 'completed'"

        # --- detail rows: every receipt in range, any status/type ---
        rows_sql = text(
            f"""
            SELECT
              s.completed_at, s.receipt_number, s.status, s.sale_type,
              s.total_amount AS gross, s.currency,
              b.name AS branch_name,
              u.full_name AS cashier_name,
              COALESCE(
                (SELECT SUM(si.discount_amount) FROM sale_item si WHERE si.sale_id = s.id), 0
              ) AS discount,
              (SELECT CASE
                        WHEN COUNT(DISTINCT p.payment_method) = 0 THEN 'none'
                        WHEN COUNT(DISTINCT p.payment_method) > 1 THEN 'mixed'
                        ELSE MIN(p.payment_method) END
                 FROM sale_payment p WHERE p.sale_id = s.id) AS payment_method
            FROM sale s
            LEFT JOIN branch b ON b.id = s.branch_id
            LEFT JOIN app_user u ON u.id = s.cashier_user_id
            WHERE {where}
            ORDER BY s.completed_at ASC
            """
        )
        rows = [dict(r) for r in (await self.session.execute(rows_sql, params)).mappings().all()]

        # --- headline totals ---
        totals_sql = text(
            f"""
            SELECT
              COALESCE(SUM(s.total_amount) FILTER (
                WHERE s.sale_type = 'sale' AND s.status = 'completed'), 0) AS gross_sales,
              COALESCE(SUM(s.total_amount) FILTER (
                WHERE s.sale_type = 'return' AND s.status = 'completed'), 0) AS total_refunds,
              COUNT(*) FILTER (
                WHERE s.sale_type = 'sale' AND s.status = 'completed') AS sales_count,
              COUNT(*) FILTER (
                WHERE s.sale_type = 'return' AND s.status = 'completed') AS returns_count
            FROM sale s WHERE {where}
            """
        )
        t = (await self.session.execute(totals_sql, params)).mappings().one()

        disc_sql = text(
            f"""
            SELECT COALESCE(SUM(si.discount_amount), 0) AS d
            FROM sale_item si JOIN sale s ON s.id = si.sale_id
            WHERE {fwd_done}
            """
        )
        total_discounts = (await self.session.execute(disc_sql, params)).scalar_one()

        # --- payment breakdown over forward completed sales ---
        bd_sql = text(
            f"""
            SELECT method, COALESCE(SUM(amt), 0) AS total FROM (
              SELECT s.total_amount AS amt,
                     CASE WHEN COUNT(DISTINCT p.payment_method) > 1 THEN 'mixed'
                          ELSE MIN(p.payment_method) END AS method
              FROM sale s LEFT JOIN sale_payment p ON p.sale_id = s.id
              WHERE {fwd_done}
              GROUP BY s.id, s.total_amount
            ) q GROUP BY method
            """
        )
        breakdown: dict[str, Decimal] = {
            "cash": Decimal("0"),
            "card": Decimal("0"),
            "bank_transfer": Decimal("0"),
            "mixed": Decimal("0"),
        }
        for method, total in (await self.session.execute(bd_sql, params)).all():
            if method in breakdown:
                breakdown[method] = Decimal(str(total))

        return {
            "rows": rows,
            "gross_sales": Decimal(str(t["gross_sales"])),
            "total_refunds": Decimal(str(t["total_refunds"])),
            "total_discounts": Decimal(str(total_discounts)),
            "sales_count": int(t["sales_count"]),
            "returns_count": int(t["returns_count"]),
            "payment_breakdown": breakdown,
        }
