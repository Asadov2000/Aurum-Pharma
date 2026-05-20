"""DB access for the POS domain."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select
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

    # -------- batch lock (concurrent-safe complete) --------

    async def lock_batch(self, batch_id: UUID) -> Batch | None:
        """SELECT ... FOR UPDATE the batch row so a concurrent complete can't
        race us into a negative qty_remaining. Returns the fresh row."""
        stmt = select(Batch).where(Batch.id == batch_id).with_for_update()
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
