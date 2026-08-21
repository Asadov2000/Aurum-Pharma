"""DB access for the POS domain."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, delete, distinct, exists, func, literal, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import local_day_range
from app.domains.auth.models import AppUser
from app.domains.catalog.models import TenantCatalog
from app.domains.foundation.models import Register
from app.domains.inventory.models import Batch
from app.domains.pos.models import (
    POSCommand,
    POSFavorite,
    POSPaymentAttempt,
    POSRefundAttempt,
    POSRefundReference,
    PrescriptionLog,
    Sale,
    SaleItem,
    SalePayment,
    Shift,
)


@dataclass(frozen=True, slots=True)
class SaleLifecycle:
    status: str
    voided_at: datetime | None
    voided_by_sale_id: UUID | None


@dataclass(frozen=True, slots=True)
class FavoriteCatalogRow:
    favorite: POSFavorite
    catalog: TenantCatalog
    stock_available: Decimal


_FULL_REFUND_SQL = """
  s.sale_type = 'sale'
  AND EXISTS (
    SELECT 1
    FROM sale_item original_item
    WHERE original_item.sale_id = s.id
  )
  AND NOT EXISTS (
    SELECT 1
    FROM sale_item original_item
    WHERE original_item.sale_id = s.id
      AND original_item.qty <> COALESCE(
        (
          SELECT SUM(return_item.qty)
          FROM sale_item return_item
          JOIN sale return_sale
            ON return_sale.id = return_item.sale_id
           AND return_sale.parent_sale_id = s.id
           AND return_sale.sale_type = 'return'
           AND return_sale.status = 'completed'
          WHERE return_item.parent_sale_item_id = original_item.id
        ),
        0
      )
  )
"""


class POSRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_user_display_name(self, user_id: UUID) -> str | None:
        result = await self.session.execute(select(AppUser.full_name).where(AppUser.id == user_id))
        return result.scalar_one_or_none()

    # -------- personal POS favorites --------

    async def list_favorites(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        branch_id: UUID,
    ) -> list[FavoriteCatalogRow]:
        stock = (
            select(
                Batch.catalog_id.label("catalog_id"),
                func.coalesce(func.sum(Batch.qty_remaining), 0).label("stock_available"),
            )
            .where(
                Batch.branch_id == branch_id,
                Batch.is_blocked.is_(False),
            )
            .group_by(Batch.catalog_id)
            .subquery()
        )
        stmt = (
            select(
                POSFavorite,
                TenantCatalog,
                func.coalesce(stock.c.stock_available, 0),
            )
            .join(
                TenantCatalog,
                and_(
                    TenantCatalog.id == POSFavorite.catalog_id,
                    TenantCatalog.tenant_id == POSFavorite.tenant_id,
                ),
            )
            .outerjoin(stock, stock.c.catalog_id == POSFavorite.catalog_id)
            .where(
                POSFavorite.tenant_id == tenant_id,
                POSFavorite.user_id == user_id,
                TenantCatalog.deleted_at.is_(None),
            )
            .order_by(POSFavorite.created_at.desc(), POSFavorite.id.desc())
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            FavoriteCatalogRow(
                favorite=favorite,
                catalog=catalog,
                stock_available=Decimal(str(stock_available)),
            )
            for favorite, catalog, stock_available in rows
        ]

    async def add_favorite(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        catalog_id: UUID,
    ) -> POSFavorite:
        stmt = (
            pg_insert(POSFavorite)
            .values(
                tenant_id=tenant_id,
                user_id=user_id,
                catalog_id=catalog_id,
                created_by=user_id,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    POSFavorite.tenant_id,
                    POSFavorite.user_id,
                    POSFavorite.catalog_id,
                ]
            )
            .returning(POSFavorite.id)
        )
        favorite_id = (await self.session.execute(stmt)).scalar_one_or_none()
        if favorite_id is not None:
            favorite = await self.session.get(POSFavorite, favorite_id)
        else:
            favorite = (
                await self.session.execute(
                    select(POSFavorite).where(
                        POSFavorite.tenant_id == tenant_id,
                        POSFavorite.user_id == user_id,
                        POSFavorite.catalog_id == catalog_id,
                    )
                )
            ).scalar_one_or_none()
        if favorite is None:
            raise RuntimeError("Favorite upsert did not return a row")
        return favorite

    async def remove_favorite(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        catalog_id: UUID,
    ) -> None:
        await self.session.execute(
            delete(POSFavorite).where(
                POSFavorite.tenant_id == tenant_id,
                POSFavorite.user_id == user_id,
                POSFavorite.catalog_id == catalog_id,
            )
        )

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

    async def has_active_draft_sales(self, shift_id: UUID) -> bool:
        item_exists = exists().where(SaleItem.sale_id == Sale.id)
        payment_exists = exists().where(SalePayment.sale_id == Sale.id)
        prescription_exists = exists().where(PrescriptionLog.sale_id == Sale.id)
        stmt = select(
            exists().where(
                and_(
                    Sale.shift_id == shift_id,
                    Sale.status == "draft",
                    or_(item_exists, payment_exists, prescription_exists),
                )
            )
        )
        return bool(await self.session.scalar(stmt))

    async def list_shifts(
        self,
        *,
        tenant_id: UUID,
        status: str | None,
        branch_id: UUID | None,
        register_id: UUID | None,
        cashier_id: UUID | None,
        cashier_query: str | None,
        date_from: date | None,
        date_to: date | None,
        branch_ids: set[UUID] | None,
        page: int,
        page_size: int,
        tz: str,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses = ["sh.tenant_id = :tenant_id"]
        params: dict[str, Any] = {"tenant_id": str(tenant_id)}

        if status is not None:
            clauses.append("sh.status = :status")
            params["status"] = status
        if branch_id is not None:
            clauses.append("sh.branch_id = :branch_id")
            params["branch_id"] = str(branch_id)
        if branch_ids is not None:
            clauses.append(
                self._branch_scope_predicate(
                    params,
                    branch_ids,
                    prefix="shift_branch",
                    column="sh.branch_id",
                )
            )
        if register_id is not None:
            clauses.append("sh.register_id = :register_id")
            params["register_id"] = str(register_id)
        if cashier_id is not None:
            clauses.append("sh.opened_by_user_id = :cashier_id")
            params["cashier_id"] = str(cashier_id)
        if cashier_query is not None:
            clauses.append("strpos(lower(u.full_name), lower(:cashier_query)) > 0")
            params["cashier_query"] = cashier_query
        if date_from is not None:
            start, _ = local_day_range(date_from, tz)
            clauses.append("sh.opened_at >= :date_from")
            params["date_from"] = start
        if date_to is not None:
            _, end = local_day_range(date_to, tz)
            clauses.append("sh.opened_at < :date_to")
            params["date_to"] = end

        where = " AND ".join(clauses)
        joins = """
            FROM shift AS sh
            JOIN branch AS b ON b.id = sh.branch_id
            JOIN register AS reg ON reg.id = sh.register_id
            LEFT JOIN app_user AS u ON u.id = sh.opened_by_user_id
        """
        total = int(
            (
                await self.session.execute(
                    text(f"SELECT count(*) {joins} WHERE {where}"),
                    params,
                )
            ).scalar_one()
        )

        page_params = {
            **params,
            "limit": page_size,
            "offset": (page - 1) * page_size,
        }
        rows = (
            await self.session.execute(
                text(f"""
                    SELECT
                      sh.id,
                      sh.branch_id,
                      b.name AS branch_name,
                      sh.register_id,
                      reg.name AS register_name,
                      sh.opened_by_user_id AS cashier_user_id,
                      u.full_name AS cashier_name,
                      sh.opened_at,
                      sh.closed_at,
                      sh.status,
                      sh.opening_cash,
                      sh.closing_cash_actual,
                      sh.closing_cash_expected,
                      sh.closing_difference,
                      shift_sales.sales_total,
                      shift_sales.returns_total,
                      COALESCE((sh.totals ->> 'sales_count')::integer, 0) AS sales_count,
                      COALESCE((sh.totals ->> 'returns_count')::integer, 0)
                        AS returns_count,
                      sh.currency
                    {joins}
                    LEFT JOIN LATERAL (
                      SELECT
                        COALESCE(
                          SUM(sale.total_amount) FILTER (
                            WHERE sale.sale_type = 'sale'
                              AND sale.status IN ('completed', 'voided')
                          ),
                          0
                        ) AS sales_total,
                        COALESCE(
                          SUM(sale.total_amount) FILTER (
                            WHERE sale.sale_type = 'return'
                              AND sale.status = 'completed'
                          ),
                          0
                        ) AS returns_total
                      FROM sale
                      WHERE sale.shift_id = sh.id
                        AND sale.is_test = false
                    ) AS shift_sales ON true
                    WHERE {where}
                    ORDER BY sh.opened_at DESC, sh.id DESC
                    LIMIT :limit OFFSET :offset
                """),
                page_params,
            )
        ).mappings()
        return [dict(row) for row in rows], total

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

    async def get_pos_command(
        self,
        *,
        tenant_id: UUID,
        operation_id: UUID,
    ) -> POSCommand | None:
        stmt = select(POSCommand).where(
            POSCommand.tenant_id == tenant_id,
            POSCommand.operation_id == operation_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_owned_pos_command(
        self,
        *,
        tenant_id: UUID,
        operation_id: UUID,
        actor_user_id: UUID,
    ) -> POSCommand | None:
        stmt = select(POSCommand).where(
            POSCommand.tenant_id == tenant_id,
            POSCommand.operation_id == operation_id,
            POSCommand.actor_user_id == actor_user_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def insert_pos_command(self, **fields: Any) -> POSCommand:
        command = POSCommand(**fields)
        self.session.add(command)
        await self.session.flush()
        await self.session.refresh(command)
        return command

    async def has_legacy_pos_operation(
        self,
        *,
        tenant_id: UUID,
        operation_id: UUID,
    ) -> bool:
        stmt = select(
            or_(
                exists().where(
                    Sale.tenant_id == tenant_id,
                    Sale.operation_id == operation_id,
                ),
                exists().where(
                    SalePayment.tenant_id == tenant_id,
                    SalePayment.operation_id == operation_id,
                ),
                exists().where(
                    POSPaymentAttempt.tenant_id == tenant_id,
                    POSPaymentAttempt.operation_id == operation_id,
                ),
                exists().where(
                    POSRefundAttempt.tenant_id == tenant_id,
                    POSRefundAttempt.operation_id == operation_id,
                ),
            )
        )
        return bool(await self.session.scalar(stmt))

    async def update_sale(self, sale: Sale, **fields: Any) -> Sale:
        for k, v in fields.items():
            setattr(sale, k, v)
        await self.session.flush()
        await self.session.refresh(sale)
        return sale

    async def sale_lifecycle(self, sale: Sale) -> SaleLifecycle:
        if sale.status != "completed" or sale.sale_type != "sale":
            return SaleLifecycle(
                status=sale.status,
                voided_at=sale.voided_at,
                voided_by_sale_id=sale.voided_by_sale_id,
            )

        row = (
            (
                await self.session.execute(
                    text(f"""
                    SELECT
                      ({_FULL_REFUND_SQL}) AS fully_refunded,
                      final_return.completed_at AS voided_at,
                      final_return.id AS voided_by_sale_id
                    FROM sale s
                    LEFT JOIN LATERAL (
                      SELECT return_sale.id, return_sale.completed_at
                      FROM sale return_sale
                      WHERE return_sale.parent_sale_id = s.id
                        AND return_sale.sale_type = 'return'
                        AND return_sale.status = 'completed'
                      ORDER BY return_sale.completed_at DESC NULLS LAST, return_sale.id DESC
                      LIMIT 1
                    ) final_return ON true
                    WHERE s.id = :sale_id
                    """),
                    {"sale_id": sale.id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None or not bool(row["fully_refunded"]):
            return SaleLifecycle(
                status=sale.status,
                voided_at=sale.voided_at,
                voided_by_sale_id=sale.voided_by_sale_id,
            )
        return SaleLifecycle(
            status="voided",
            voided_at=row["voided_at"],
            voided_by_sale_id=row["voided_by_sale_id"],
        )

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

    async def delete_items(self, sale_id: UUID) -> int:
        from sqlalchemy import delete

        result = await self.session.execute(delete(SaleItem).where(SaleItem.sale_id == sale_id))
        return result.rowcount or 0  # type: ignore[attr-defined]

    async def next_item_position(self, sale_id: UUID) -> int:
        stmt = select(func.coalesce(func.max(SaleItem.position), 0)).where(
            SaleItem.sale_id == sale_id
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one()) + 1

    # -------- server-trusted payment attempts --------

    async def get_payment_attempt(self, attempt_id: UUID) -> POSPaymentAttempt | None:
        return await self.session.get(POSPaymentAttempt, attempt_id)

    async def lock_payment_attempt(self, attempt_id: UUID) -> POSPaymentAttempt | None:
        stmt = (
            select(POSPaymentAttempt)
            .where(POSPaymentAttempt.id == attempt_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def lock_active_payment_attempts_for_sale(
        self,
        sale_id: UUID,
    ) -> list[POSPaymentAttempt]:
        stmt = (
            select(POSPaymentAttempt)
            .where(
                POSPaymentAttempt.sale_id == sale_id,
                POSPaymentAttempt.status.in_(("pending", "confirmed")),
            )
            .order_by(POSPaymentAttempt.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_payment_attempt_by_operation_id(
        self,
        *,
        tenant_id: UUID,
        operation_id: UUID,
    ) -> POSPaymentAttempt | None:
        stmt = select(POSPaymentAttempt).where(
            POSPaymentAttempt.tenant_id == tenant_id,
            POSPaymentAttempt.operation_id == operation_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def insert_payment_attempt(self, **fields: Any) -> POSPaymentAttempt:
        attempt = POSPaymentAttempt(**fields)
        self.session.add(attempt)
        await self.session.flush()
        await self.session.refresh(attempt)
        return attempt

    async def update_payment_attempt(
        self,
        attempt: POSPaymentAttempt,
        **fields: Any,
    ) -> POSPaymentAttempt:
        for key, value in fields.items():
            setattr(attempt, key, value)
        await self.session.flush()
        await self.session.refresh(attempt)
        return attempt

    # -------- server-controlled electronic refund attempts --------

    async def get_refund_attempt(self, attempt_id: UUID) -> POSRefundAttempt | None:
        return await self.session.get(POSRefundAttempt, attempt_id)

    async def lock_refund_attempt(self, attempt_id: UUID) -> POSRefundAttempt | None:
        stmt = (
            select(POSRefundAttempt)
            .where(POSRefundAttempt.id == attempt_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def lock_active_refund_attempt_for_sale(
        self,
        parent_sale_id: UUID,
    ) -> POSRefundAttempt | None:
        stmt = (
            select(POSRefundAttempt)
            .where(
                POSRefundAttempt.parent_sale_id == parent_sale_id,
                POSRefundAttempt.status.in_(("pending", "confirmed")),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_refund_attempt_by_operation_id(
        self,
        *,
        tenant_id: UUID,
        operation_id: UUID,
    ) -> POSRefundAttempt | None:
        stmt = select(POSRefundAttempt).where(
            POSRefundAttempt.tenant_id == tenant_id,
            POSRefundAttempt.operation_id == operation_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def insert_refund_attempt(self, **fields: Any) -> POSRefundAttempt:
        attempt = POSRefundAttempt(**fields)
        self.session.add(attempt)
        await self.session.flush()
        await self.session.refresh(attempt)
        return attempt

    async def update_refund_attempt(
        self,
        attempt: POSRefundAttempt,
        **fields: Any,
    ) -> POSRefundAttempt:
        for key, value in fields.items():
            setattr(attempt, key, value)
        await self.session.flush()
        await self.session.refresh(attempt)
        return attempt

    async def list_refund_references(
        self,
        attempt_id: UUID,
    ) -> list[POSRefundReference]:
        stmt = (
            select(POSRefundReference)
            .where(POSRefundReference.refund_attempt_id == attempt_id)
            .order_by(POSRefundReference.payment_method)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def insert_refund_reference(self, **fields: Any) -> POSRefundReference:
        reference = POSRefundReference(**fields)
        self.session.add(reference)
        await self.session.flush()
        await self.session.refresh(reference)
        return reference

    async def has_active_refund_attempts_for_register(self, register_id: UUID) -> bool:
        stmt = select(
            exists().where(
                POSRefundAttempt.register_id == register_id,
                POSRefundAttempt.status.in_(("pending", "confirmed")),
            )
        )
        return bool(await self.session.scalar(stmt))

    # -------- payments --------

    async def get_payment_by_operation_id(
        self,
        *,
        tenant_id: UUID,
        operation_id: UUID,
    ) -> SalePayment | None:
        stmt = select(SalePayment).where(
            SalePayment.tenant_id == tenant_id,
            SalePayment.operation_id == operation_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def insert_payment(self, **fields: Any) -> SalePayment:
        p = SalePayment(**fields)
        self.session.add(p)
        await self.session.flush()
        await self.session.refresh(p)
        return p

    async def list_payments(self, sale_id: UUID) -> list[SalePayment]:
        stmt = (
            select(SalePayment)
            .where(SalePayment.sale_id == sale_id)
            .order_by(SalePayment.created_at, SalePayment.id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def payments_total(self, sale_id: UUID) -> Decimal:
        stmt = select(func.coalesce(func.sum(SalePayment.amount), 0)).where(
            SalePayment.sale_id == sale_id
        )
        result = await self.session.execute(stmt)
        return Decimal(str(result.scalar_one()))

    async def payment_methods(self, sale_id: UUID) -> set[str]:
        stmt = select(SalePayment.payment_method).where(SalePayment.sale_id == sale_id).distinct()
        result = await self.session.execute(stmt)
        return set(result.scalars().all())

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
        """Allocate the next register-wide number inside the sale transaction."""
        scope = (
            await self.session.execute(
                select(Register.tenant_id, Register.branch_id).where(Register.id == register_id)
            )
        ).one_or_none()
        if scope is None:
            return None
        await self.session.execute(
            text("SELECT set_config('app.branch_id', :branch_id, true)"),
            {"branch_id": str(scope.branch_id)},
        )
        result = await self.session.execute(
            text(
                "SELECT receipt_seq, receipt_number "
                "FROM public.allocate_register_receipt(:tenant_id, :register_id)"
            ),
            {
                "tenant_id": scope.tenant_id,
                "register_id": register_id,
            },
        )
        row = result.one_or_none()
        if row is None:
            return None
        return int(row.receipt_seq), str(row.receipt_number)

    async def refunded_line_totals(
        self,
        parent_sale_id: UUID,
    ) -> dict[UUID, tuple[Decimal, Decimal, Decimal]]:
        stmt = (
            select(
                SaleItem.parent_sale_item_id,
                func.sum(SaleItem.qty),
                func.sum(SaleItem.total_price),
                func.sum(SaleItem.discount_amount),
            )
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
            parent_item_id: (
                Decimal(str(qty)),
                Decimal(str(total_price)),
                Decimal(str(discount_amount)),
            )
            for parent_item_id, qty, total_price, discount_amount in rows
            if parent_item_id is not None
        }

    async def refunded_quantities(self, parent_sale_id: UUID) -> dict[UUID, Decimal]:
        return {
            item_id: values[0]
            for item_id, values in (await self.refunded_line_totals(parent_sale_id)).items()
        }

    async def refunded_payment_totals(self, parent_sale_id: UUID) -> dict[str, Decimal]:
        stmt = (
            select(
                SalePayment.payment_method,
                func.sum(SalePayment.amount),
            )
            .join(Sale, Sale.id == SalePayment.sale_id)
            .where(
                Sale.parent_sale_id == parent_sale_id,
                Sale.sale_type == "return",
                Sale.status == "completed",
            )
            .group_by(SalePayment.payment_method)
        )
        rows = (await self.session.execute(stmt)).all()
        return {str(payment_method): Decimal(str(amount)) for payment_method, amount in rows}

    async def shift_totals(self, shift_id: UUID) -> dict[str, Any]:
        """Aggregates payments grouped by method for sales in this shift,
        plus sales_count / returns_count. Test (is_test) sales are excluded —
        they never represent real money."""
        signed_payment = case(
            (Sale.sale_type == "return", -SalePayment.amount),
            else_=SalePayment.amount,
        )
        payment_sum_stmt = (
            select(SalePayment.payment_method, func.coalesce(func.sum(signed_payment), 0))
            .join(Sale, Sale.id == SalePayment.sale_id)
            .where(
                and_(
                    Sale.shift_id == shift_id,
                    Sale.status.in_(("completed", "voided")),
                    Sale.is_test.is_(False),
                )
            )
            .group_by(SalePayment.payment_method)
        )
        result = await self.session.execute(payment_sum_stmt)
        totals: dict[str, Any] = {
            "cash": "0",
            "card": "0",
            "qr": "0",
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
                    Sale.status.in_(("completed", "voided")),
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
            "qr": Decimal("0"),
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

    async def lock_catalog_items(
        self,
        *,
        tenant_id: UUID,
        catalog_ids: set[UUID],
    ) -> list[TenantCatalog]:
        stmt = (
            select(TenantCatalog)
            .where(
                TenantCatalog.tenant_id == tenant_id,
                TenantCatalog.id.in_(catalog_ids),
            )
            .order_by(TenantCatalog.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return list((await self.session.execute(stmt)).scalars().all())

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
        clauses.append(
            POSRepository._branch_scope_predicate(
                params,
                branch_ids,
                prefix="allowed_branch",
            )
        )

    @staticmethod
    def _branch_scope_predicate(
        params: dict[str, Any],
        branch_ids: set[UUID],
        *,
        prefix: str,
        column: str = "s.branch_id",
    ) -> str:
        if not branch_ids:
            return "1 = 0"
        branch_keys: list[str] = []
        for idx, allowed_branch_id in enumerate(sorted(branch_ids, key=str)):
            key = f"{prefix}_{idx}"
            branch_keys.append(f":{key}")
            params[key] = str(allowed_branch_id)
        return f"{column} IN ({', '.join(branch_keys)})"

    @staticmethod
    def _append_sales_visibility_clause(
        clauses: list[str],
        params: dict[str, Any],
        *,
        viewer_id: UUID | None,
        own_branch_ids: set[UUID] | None,
        tenant_view_branch_ids: set[UUID] | None,
        can_view_tenant: bool,
    ) -> None:
        if viewer_id is None or can_view_tenant:
            return

        params["viewer"] = str(viewer_id)
        authorization_clauses: list[str] = []
        if own_branch_ids is None:
            authorization_clauses.append("s.cashier_user_id = :viewer")
        elif own_branch_ids:
            own_scope = POSRepository._branch_scope_predicate(
                params,
                own_branch_ids,
                prefix="own_branch",
            )
            authorization_clauses.append(f"(s.cashier_user_id = :viewer AND {own_scope})")

        if tenant_view_branch_ids is None:
            authorization_clauses.append("1 = 1")
        elif tenant_view_branch_ids:
            authorization_clauses.append(
                POSRepository._branch_scope_predicate(
                    params,
                    tenant_view_branch_ids,
                    prefix="tenant_view_branch",
                )
            )

        clauses.append(
            f"({' OR '.join(authorization_clauses)})" if authorization_clauses else "1 = 0"
        )

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
        viewer_id: UUID | None = None,
        own_branch_ids: set[UUID] | None = None,
        tenant_view_branch_ids: set[UUID] | None = None,
        can_view_tenant: bool = False,
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
        self._append_sales_visibility_clause(
            clauses,
            params,
            viewer_id=viewer_id,
            own_branch_ids=own_branch_ids,
            tenant_view_branch_ids=tenant_view_branch_ids,
            can_view_tenant=can_view_tenant,
        )
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
              s.id, s.receipt_number, s.completed_at,
              CASE
                WHEN s.status = 'voided'
                  OR (s.status = 'completed' AND ({_FULL_REFUND_SQL}))
                THEN 'voided'
                ELSE s.status
              END AS status,
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
            ORDER BY s.completed_at DESC, s.id DESC
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
        include_rows: bool = True,
        include_daily: bool = False,
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

        rows: list[dict[str, Any]] = []
        if include_rows:
            # Detail rows are needed by XLSX, but the screen overview deliberately
            # skips them to stay fast over weak connections and long periods.
            rows_sql = text(f"""
                SELECT
                  s.completed_at, s.receipt_number,
                  CASE
                    WHEN s.status = 'voided'
                      OR (s.status = 'completed' AND ({_FULL_REFUND_SQL}))
                    THEN 'voided'
                    ELSE s.status
                  END AS status,
                  s.sale_type,
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
            rows = [
                dict(r) for r in (await self.session.execute(rows_sql, params)).mappings().all()
            ]

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
            "qr": Decimal("0"),
            "bank_transfer": Decimal("0"),
            "mixed": Decimal("0"),
        }
        for method, total in (await self.session.execute(bd_sql, params)).all():
            if method in breakdown:
                breakdown[method] = Decimal(str(total))

        daily: list[dict[str, Any]] = []
        if include_daily:
            daily_params = {**params, "report_tz": tz}
            daily_sql = text(f"""
                SELECT
                  timezone(:report_tz, s.completed_at)::date AS day,
                  COALESCE(SUM(s.total_amount) FILTER (
                    WHERE s.sale_type = 'sale'), 0) AS gross_sales,
                  COALESCE(SUM(s.total_amount) FILTER (
                    WHERE s.sale_type = 'return'), 0) AS total_refunds,
                  COALESCE(SUM(
                    CASE WHEN s.sale_type = 'sale' THEN
                      COALESCE((
                        SELECT SUM(si.discount_amount)
                        FROM sale_item si
                        WHERE si.sale_id = s.id
                      ), 0)
                    ELSE 0 END
                  ), 0) AS total_discounts,
                  COUNT(*) FILTER (WHERE s.sale_type = 'sale') AS sales_count,
                  COUNT(*) FILTER (WHERE s.sale_type = 'return') AS returns_count
                FROM sale s
                WHERE {where}
                GROUP BY timezone(:report_tz, s.completed_at)::date
                ORDER BY day
                """)
            daily = [
                dict(r)
                for r in (await self.session.execute(daily_sql, daily_params)).mappings().all()
            ]

        return {
            "rows": rows,
            "gross_sales": Decimal(str(t["gross_sales"])),
            "total_refunds": Decimal(str(t["total_refunds"])),
            "total_discounts": Decimal(str(total_discounts)),
            "sales_count": int(t["sales_count"]),
            "returns_count": int(t["returns_count"]),
            "payment_breakdown": breakdown,
            "daily": daily,
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
