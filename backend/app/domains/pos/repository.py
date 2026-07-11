"""DB access for the POS domain."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, and_, case, cast, distinct, func, literal, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import local_day_range
from app.domains.foundation.models import Register
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

    async def lock_open_shift_for_register(self, register_id: UUID) -> Shift | None:
        stmt = (
            select(Shift)
            .where(and_(Shift.register_id == register_id, Shift.status == "open"))
            .limit(1)
            .with_for_update()
            .execution_options(populate_existing=True)
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

    async def lock_shift(self, shift_id: UUID) -> Shift | None:
        stmt = (
            select(Shift)
            .where(Shift.id == shift_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

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

    async def lock_sale(self, sale_id: UUID) -> Sale | None:
        stmt = (
            select(Sale)
            .where(Sale.id == sale_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_sale_by_operation_id(
        self,
        *,
        tenant_id: UUID,
        operation_id: UUID,
    ) -> Sale | None:
        stmt = select(Sale).where(
            Sale.tenant_id == tenant_id,
            Sale.operation_id == operation_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def lock_operation_id(self, operation_id: UUID) -> None:
        await self.session.execute(
            text(
                "SELECT pg_advisory_xact_lock(" "hashtextextended(CAST(:operation_id AS TEXT), 0))"
            ),
            {"operation_id": str(operation_id)},
        )

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

    async def list_items_with_batch(
        self, sale_id: UUID
    ) -> list[tuple[SaleItem, str | None, date | None, int | None]]:
        """Read-only enrichment for the sale view: each line plus its batch
        number, expiry and days-to-expiry (PG `date - date` → int days). One
        join, no extra logic — the FEFO-chosen batch_id is already on the row.
        Left join so a line never disappears if its batch is gone."""
        stmt = (
            select(
                SaleItem,
                Batch.batch_number,
                Batch.expires_at,
                (Batch.expires_at - func.current_date()),
            )
            .join(Batch, Batch.id == SaleItem.batch_id, isouter=True)
            .where(SaleItem.sale_id == sale_id)
            .order_by(SaleItem.position.asc())
        )
        rows = (await self.session.execute(stmt)).all()
        return [(r[0], r[1], r[2], r[3]) for r in rows]

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

    async def allocate_receipt_number(self, register_id: UUID) -> tuple[int, str] | None:
        """Allocate a register-wide number while holding the register row lock.

        Historical demo data may have reset receipt_number on every shift. The
        internal receipt_seq was backfilled uniquely, while numeric legacy
        numbers are also considered so a newly displayed number never reuses
        one of them.
        """
        register_stmt = select(Register.id).where(Register.id == register_id).with_for_update()
        register_id_result = await self.session.execute(register_stmt)
        if register_id_result.scalar_one_or_none() is None:
            return None

        numeric_receipt = case(
            (
                Sale.receipt_number.op("~")(r"^[0-9]{1,18}$"),
                cast(Sale.receipt_number, BigInteger),
            ),
            else_=0,
        )
        stmt = select(
            func.greatest(
                func.coalesce(func.max(Sale.receipt_seq), 0),
                func.coalesce(func.max(numeric_receipt), 0),
            )
            + 1
        ).where(Sale.register_id == register_id)
        seq = int((await self.session.execute(stmt)).scalar_one())
        return seq, f"{seq:06d}"

    async def refunded_quantities(self, parent_sale_id: UUID) -> dict[UUID, Decimal]:
        stmt = (
            select(SaleItem.parent_sale_item_id, func.sum(SaleItem.qty))
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(
                Sale.parent_sale_id == parent_sale_id,
                Sale.sale_type == "return",
                Sale.status == "completed",
                SaleItem.parent_sale_item_id.is_not(None),
            )
            .group_by(SaleItem.parent_sale_item_id)
        )
        rows = (await self.session.execute(stmt)).all()
        return {
            parent_item_id: Decimal(str(qty))
            for parent_item_id, qty in rows
            if parent_item_id is not None
        }

    async def shift_totals(self, shift_id: UUID) -> dict[str, Any]:
        """Aggregates payments grouped by method for sales in this shift,
        plus sales_count / returns_count. Test (is_test) sales are excluded —
        they never represent real money."""
        payment_sum_stmt = (
            select(SalePayment.payment_method, func.coalesce(func.sum(SalePayment.amount), 0))
            .join(Sale, Sale.id == SalePayment.sale_id)
            .where(
                and_(
                    Sale.shift_id == shift_id,
                    Sale.status == "completed",
                    Sale.is_test.is_(False),
                )
            )
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
                    Sale.is_test.is_(False),
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
                    Sale.is_test.is_(False),
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
        bucketed by its method (≥2 distinct methods → 'mixed').

        Forward sales count when completed OR later voided by a full refund —
        the sale economically happened; its refund is counted under returns. So
        gross and refunds stay consistent (a same-shift full refund shows
        gross=amount AND refunds=amount, not gross=0/refunds=amount). Test
        (is_test) sales are excluded — they aren't real money."""

        fwd_statuses = ("completed", "voided")

        def _fwd() -> Any:
            return and_(
                Sale.shift_id == shift_id,
                Sale.status.in_(fwd_statuses),
                Sale.sale_type == "sale",
                Sale.is_test.is_(False),
            )

        def _ret() -> Any:
            return and_(
                Sale.shift_id == shift_id,
                Sale.status == "completed",
                Sale.sale_type == "return",
                Sale.is_test.is_(False),
            )

        sales_stmt = select(func.coalesce(func.sum(Sale.total_amount), 0), func.count()).where(
            _fwd()
        )
        total_sales, sales_count = (await self.session.execute(sales_stmt)).one()

        refunds_stmt = select(func.coalesce(func.sum(Sale.total_amount), 0), func.count()).where(
            _ret()
        )
        total_refunds, returns_count = (await self.session.execute(refunds_stmt)).one()

        disc_stmt = (
            select(func.coalesce(func.sum(SaleItem.discount_amount), 0))
            .select_from(SaleItem)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(_fwd())
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
            .where(_fwd())
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

    @staticmethod
    def _append_branch_scope_clause(
        clauses: list[str],
        params: dict[str, Any],
        branch_ids: set[UUID] | None,
    ) -> None:
        if branch_ids is None:
            return
        if not branch_ids:
            clauses.append("1 = 0")
            return
        branch_keys: list[str] = []
        for idx, allowed_branch_id in enumerate(sorted(branch_ids, key=str)):
            key = f"allowed_branch_{idx}"
            branch_keys.append(f":{key}")
            params[key] = str(allowed_branch_id)
        clauses.append(f"s.branch_id IN ({', '.join(branch_keys)})")

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
        branch_ids: set[UUID] | None = None,
        page: int,
        page_size: int,
        tz: str = "Asia/Dushanbe",
    ) -> tuple[list[dict[str, Any]], int]:
        """One query joins sale → branch/register/cashier for resolved names,
        aggregates payment methods, builds a short item preview, and derives
        is_refund / has_refund / refund_receipt_number from the parent↔child
        sale link. Only receipts (completed/voided, completed_at set) appear —
        drafts have no receipt_number. Test (is_test) sales are excluded — they
        aren't real receipts. Date filters use the tenant timezone `tz`.

        `has_refund` is true when at least one completed return-sale points at
        this row; `refund_receipt_number` is that return's receipt. Deriving
        these avoids a denormalized column that could drift.
        """
        clauses = ["s.tenant_id = :tid", "s.completed_at IS NOT NULL", "s.is_test = false"]
        params: dict[str, Any] = {"tid": str(tenant_id)}

        if cashier_id is not None:
            clauses.append("s.cashier_user_id = :cashier")
            params["cashier"] = str(cashier_id)
        if branch_id is not None:
            clauses.append("s.branch_id = :branch")
            params["branch"] = str(branch_id)
        self._append_branch_scope_clause(clauses, params, branch_ids)
        if register_id is not None:
            clauses.append("s.register_id = :register")
            params["register"] = str(register_id)
        if receipt_number is not None:
            clauses.append("s.receipt_number = :receipt")
            params["receipt"] = receipt_number
        if date_from is not None:
            start, _ = local_day_range(date_from, tz)
            clauses.append("s.completed_at >= :dfrom_ts")
            params["dfrom_ts"] = start
        if date_to is not None:
            _, end = local_day_range(date_to, tz)  # exclusive end = next local day
            clauses.append("s.completed_at < :dto_ts")
            params["dto_ts"] = end
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
        rows_sql = text(f"""
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
            """)
        rows = (await self.session.execute(rows_sql, params)).mappings().all()
        return [dict(r) for r in rows], total

    async def sales_summary(
        self,
        *,
        tenant_id: UUID,
        date_from: Any,
        date_to: Any,
        branch_id: UUID | None,
        tz: str = "Asia/Dushanbe",
    ) -> dict[str, Any]:
        """Detail rows + period totals for the accountant summary over
        [date_from, date_to]. A forward sale counts toward gross/discounts/
        payment-breakdown if it has completed_at — INCLUDING one later voided by
        a full refund (the sale economically happened; its refund is counted
        separately under returns). This keeps `net = gross − discounts −
        refunds` equal to the money actually kept: a sale fully refunded in the
        same period nets to 0, not −refund. The payment breakdown buckets each
        forward sale by its method (≥2 distinct → 'mixed'), identical to
        z_report_aggregates, so a single shift's range reconciles with its
        Z-report.

        Dates are interpreted in the tenant timezone `tz` (so a 01:00 Dushanbe
        sale lands on the local day), and test (is_test) sales are excluded.
        """
        start, _ = local_day_range(date_from, tz)
        _, end = local_day_range(date_to, tz)  # exclusive end = start of day after date_to
        base = ["s.tenant_id = :tid", "s.completed_at IS NOT NULL", "s.is_test = false"]
        params: dict[str, Any] = {
            "tid": str(tenant_id),
            "dfrom_ts": start,
            "dto_ts": end,
        }
        base.append("s.completed_at >= :dfrom_ts")
        base.append("s.completed_at < :dto_ts")
        if branch_id is not None:
            base.append("s.branch_id = :branch")
            params["branch"] = str(branch_id)
        where = " AND ".join(base)
        # Forward sales = sale_type='sale' with completed_at set (base already
        # requires it), so completed + voided-by-refund both count.
        fwd_done = f"{where} AND s.sale_type = 'sale'"

        # --- detail rows: every receipt in range, any status/type ---
        rows_sql = text(f"""
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
            """)
        rows = [dict(r) for r in (await self.session.execute(rows_sql, params)).mappings().all()]

        # --- headline totals ---
        totals_sql = text(f"""
            SELECT
              COALESCE(SUM(s.total_amount) FILTER (
                WHERE s.sale_type = 'sale'), 0) AS gross_sales,
              COALESCE(SUM(s.total_amount) FILTER (
                WHERE s.sale_type = 'return'), 0) AS total_refunds,
              COUNT(*) FILTER (WHERE s.sale_type = 'sale') AS sales_count,
              COUNT(*) FILTER (WHERE s.sale_type = 'return') AS returns_count
            FROM sale s WHERE {where}
            """)
        t = (await self.session.execute(totals_sql, params)).mappings().one()

        disc_sql = text(f"""
            SELECT COALESCE(SUM(si.discount_amount), 0) AS d
            FROM sale_item si JOIN sale s ON s.id = si.sale_id
            WHERE {fwd_done}
            """)
        total_discounts = (await self.session.execute(disc_sql, params)).scalar_one()

        # --- payment breakdown over forward sales (completed + voided) ---
        bd_sql = text(f"""
            SELECT method, COALESCE(SUM(amt), 0) AS total FROM (
              SELECT s.total_amount AS amt,
                     CASE WHEN COUNT(DISTINCT p.payment_method) > 1 THEN 'mixed'
                          ELSE MIN(p.payment_method) END AS method
              FROM sale s LEFT JOIN sale_payment p ON p.sale_id = s.id
              WHERE {fwd_done}
              GROUP BY s.id, s.total_amount
            ) q GROUP BY method
            """)
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

    async def stock_on_date(
        self,
        *,
        tenant_id: UUID,
        on_date: Any,
        branch_id: UUID | None,
        tz: str = "Asia/Dushanbe",
    ) -> list[dict[str, Any]]:
        """Per-batch stock as of `on_date`, summed from the batch_movement
        ledger (Σ qty_delta for movements dated ≤ on_date). The ledger is the
        source of truth (the trigger keeps batch.qty_remaining == Σ qty_delta),
        so this is a faithful historical reconstruction, not just 'today'. Only
        batches with a positive balance on the date are returned. The movement
        date is taken in the tenant timezone `tz`."""
        # "as of end of on_date" = movements strictly before the start of the
        # next local day → sargable on batch_movement(created_at).
        _, on_end = local_day_range(on_date, tz)
        clauses = ["b.tenant_id = :tid"]
        params: dict[str, Any] = {"tid": str(tenant_id), "on_end": on_end}
        if branch_id is not None:
            clauses.append("b.branch_id = :branch")
            params["branch"] = str(branch_id)
        where = " AND ".join(clauses)

        sql = text(f"""
            SELECT
              tc.brand_name AS name,
              tc.inn AS inn,
              br.name AS branch_name,
              b.batch_number, b.expires_at, b.purchase_price, b.currency,
              SUM(bm.qty_delta) AS qty
            FROM batch b
            JOIN batch_movement bm
              ON bm.batch_id = b.id AND bm.created_at < :on_end
            LEFT JOIN tenant_catalog tc ON tc.id = b.catalog_id
            LEFT JOIN branch br ON br.id = b.branch_id
            WHERE {where}
            GROUP BY b.id, tc.brand_name, tc.inn, br.name,
                     b.batch_number, b.expires_at, b.purchase_price, b.currency
            HAVING SUM(bm.qty_delta) > 0
            ORDER BY tc.brand_name NULLS LAST, b.expires_at
            """)
        return [dict(r) for r in (await self.session.execute(sql, params)).mappings().all()]
