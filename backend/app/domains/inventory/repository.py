"""Database access for the inventory domain."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.catalog.models import TenantCatalog
from app.domains.foundation.models import Branch
from app.domains.inventory.expiry import ExpiryBoundaries, ExpiryStatus
from app.domains.inventory.models import Batch, BatchMovement, WriteOff


@dataclass(frozen=True, slots=True)
class BatchSearchRow:
    batch: Batch
    branch_name: str
    catalog_name: str
    catalog_form: str | None
    catalog_dosage: str | None
    catalog_pack_size: str | None
    expiry_status: ExpiryStatus
    days_to_expiry: int


@dataclass(frozen=True, slots=True)
class BatchSearchSummary:
    total: int
    total_qty: Decimal
    purchase_value: Decimal
    sale_value: Decimal
    attention_count: int
    expired_count: int
    blocked_count: int


@dataclass(frozen=True, slots=True)
class BatchDetailsRow(BatchSearchRow):
    pass


class InventoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -------------------------------------------------------------------------
    # batch (read)
    # -------------------------------------------------------------------------

    async def get_batch(self, batch_id: UUID, *, tenant_id: UUID | None = None) -> Batch | None:
        if tenant_id is None:
            return await self.session.get(Batch, batch_id)
        stmt = select(Batch).where(Batch.id == batch_id, Batch.tenant_id == tenant_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_batch_details(
        self,
        batch_id: UUID,
        *,
        tenant_id: UUID | None,
        boundaries: ExpiryBoundaries,
    ) -> BatchDetailsRow | None:
        expiry_status = _expiry_status_expression(boundaries)
        stmt = (
            select(
                Batch,
                Branch.name,
                TenantCatalog.brand_name,
                TenantCatalog.form,
                TenantCatalog.dosage,
                TenantCatalog.pack_size,
                expiry_status,
                (Batch.expires_at - boundaries.today).label("days_to_expiry"),
            )
            .join(
                Branch,
                and_(Branch.id == Batch.branch_id, Branch.tenant_id == Batch.tenant_id),
            )
            .join(
                TenantCatalog,
                and_(
                    TenantCatalog.id == Batch.catalog_id,
                    TenantCatalog.tenant_id == Batch.tenant_id,
                ),
            )
            .where(
                Batch.id == batch_id,
                *([Batch.tenant_id == tenant_id] if tenant_id is not None else []),
            )
        )
        row = (await self.session.execute(stmt)).one_or_none()
        if row is None:
            return None
        batch, branch_name, name, form, dosage, pack_size, status, days = row
        return BatchDetailsRow(
            batch=batch,
            branch_name=branch_name,
            catalog_name=name,
            catalog_form=form,
            catalog_dosage=dosage,
            catalog_pack_size=pack_size,
            expiry_status=cast(ExpiryStatus, status),
            days_to_expiry=int(days),
        )

    async def create_batch(self, **fields: Any) -> Batch:
        b = Batch(**fields)
        self.session.add(b)
        await self.session.flush()
        await self.session.refresh(b)
        return b

    async def search_with_expiry(
        self,
        *,
        catalog_id: UUID | None,
        branch_id: UUID | None,
        branch_ids: set[UUID] | None,
        expiry_status: str | None,
        batch_number: str | None,
        is_blocked: bool | None,
        show_empty: bool,
        page: int,
        page_size: int,
        tenant_id: UUID | None,
        boundaries: ExpiryBoundaries,
    ) -> tuple[list[BatchSearchRow], BatchSearchSummary]:
        clauses: list[ColumnElement[bool]] = []
        if tenant_id is not None:
            clauses.append(Batch.tenant_id == tenant_id)
        if not show_empty:
            clauses.append(Batch.qty_remaining > 0)
        if catalog_id is not None:
            clauses.append(Batch.catalog_id == catalog_id)
        if branch_id is not None:
            clauses.append(Batch.branch_id == branch_id)
        if branch_ids is not None:
            if not branch_ids:
                clauses.append(Batch.id.is_(None))
            else:
                clauses.append(Batch.branch_id.in_(sorted(branch_ids, key=str)))
        if batch_number:
            clauses.append(Batch.batch_number.icontains(batch_number.strip(), autoescape=True))
        if is_blocked is not None:
            clauses.append(Batch.is_blocked.is_(is_blocked))

        expiry_case = _expiry_status_expression(boundaries)
        if expiry_status:
            clauses.append(expiry_case == expiry_status)

        join_condition = and_(
            Branch.id == Batch.branch_id,
            Branch.tenant_id == Batch.tenant_id,
        )
        catalog_join_condition = and_(
            TenantCatalog.id == Batch.catalog_id,
            TenantCatalog.tenant_id == Batch.tenant_id,
        )
        list_stmt = (
            select(
                Batch,
                Branch.name,
                TenantCatalog.brand_name,
                TenantCatalog.form,
                TenantCatalog.dosage,
                TenantCatalog.pack_size,
                expiry_case,
                (Batch.expires_at - boundaries.today).label("days_to_expiry"),
            )
            .join(Branch, join_condition)
            .join(TenantCatalog, catalog_join_condition)
            .where(*clauses)
            .order_by(Batch.expires_at.asc(), Batch.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = (await self.session.execute(list_stmt)).all()
        items = [
            BatchSearchRow(
                batch=batch,
                branch_name=branch_name,
                catalog_name=name,
                catalog_form=form,
                catalog_dosage=dosage,
                catalog_pack_size=pack_size,
                expiry_status=cast(ExpiryStatus, status),
                days_to_expiry=int(days),
            )
            for batch, branch_name, name, form, dosage, pack_size, status, days in rows
        ]

        summary_stmt = (
            select(
                func.count().label("total"),
                func.coalesce(func.sum(Batch.qty_remaining), 0).label("total_qty"),
                func.coalesce(func.sum(Batch.qty_remaining * Batch.purchase_price), 0).label(
                    "purchase_value"
                ),
                func.coalesce(func.sum(Batch.qty_remaining * Batch.sale_price), 0).label(
                    "sale_value"
                ),
                func.count()
                .filter(
                    or_(
                        expiry_case.in_(("expired", "red", "orange")),
                        Batch.is_blocked.is_(True),
                    )
                )
                .label("attention_count"),
                func.count().filter(expiry_case == "expired").label("expired_count"),
                func.count().filter(Batch.is_blocked.is_(True)).label("blocked_count"),
            )
            .select_from(Batch)
            .join(Branch, join_condition)
            .join(TenantCatalog, catalog_join_condition)
            .where(*clauses)
        )
        summary_row = (await self.session.execute(summary_stmt)).one()
        summary = BatchSearchSummary(
            total=int(summary_row.total),
            total_qty=Decimal(str(summary_row.total_qty)),
            purchase_value=Decimal(str(summary_row.purchase_value)),
            sale_value=Decimal(str(summary_row.sale_value)),
            attention_count=int(summary_row.attention_count),
            expired_count=int(summary_row.expired_count),
            blocked_count=int(summary_row.blocked_count),
        )
        return items, summary

    # -------------------------------------------------------------------------
    # batch_movement
    # -------------------------------------------------------------------------

    async def insert_movement(self, **fields: Any) -> BatchMovement:
        m = BatchMovement(**fields)
        self.session.add(m)
        await self.session.flush()
        await self.session.refresh(m)
        return m

    async def list_movements(
        self, batch_id: UUID, *, limit: int | None = None
    ) -> list[BatchMovement]:
        stmt = (
            select(BatchMovement)
            .where(BatchMovement.batch_id == batch_id)
            .order_by(BatchMovement.created_at.desc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # -------------------------------------------------------------------------
    # write_off
    # -------------------------------------------------------------------------

    async def insert_write_off(self, **fields: Any) -> WriteOff:
        w = WriteOff(**fields)
        self.session.add(w)
        await self.session.flush()
        await self.session.refresh(w)
        return w

    async def get_write_off(
        self,
        write_off_id: UUID,
        *,
        tenant_id: UUID | None = None,
    ) -> WriteOff | None:
        if tenant_id is None:
            return await self.session.get(WriteOff, write_off_id)
        stmt = select(WriteOff).where(
            WriteOff.id == write_off_id,
            WriteOff.tenant_id == tenant_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    # -------------------------------------------------------------------------
    # FEFO selection
    # -------------------------------------------------------------------------

    async def fefo_candidates(
        self,
        *,
        catalog_id: UUID,
        branch_id: UUID,
        tenant_id: UUID,
        include_expired: bool,
        today: date,
        lock: bool = False,
    ) -> list[Batch]:
        """All non-blocked, non-empty batches for catalog+branch, sorted by
        expires_at ASC. If include_expired=False, filters expired ones out."""
        stmt = (
            select(Batch)
            .where(
                and_(
                    Batch.catalog_id == catalog_id,
                    Batch.branch_id == branch_id,
                    Batch.tenant_id == tenant_id,
                    Batch.qty_remaining > 0,
                    Batch.is_blocked.is_(False),
                )
            )
            .order_by(Batch.expires_at.asc(), Batch.created_at.asc(), Batch.id.asc())
        )
        if not include_expired:
            stmt = stmt.where(Batch.expires_at > today)
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def sum_qty_remaining(
        self,
        *,
        catalog_id: UUID,
        branch_id: UUID,
        tenant_id: UUID | None = None,
    ) -> Decimal:
        clauses = [
            Batch.catalog_id == catalog_id,
            Batch.branch_id == branch_id,
            Batch.is_blocked.is_(False),
        ]
        if tenant_id is not None:
            clauses.append(Batch.tenant_id == tenant_id)
        stmt = select(func.coalesce(func.sum(Batch.qty_remaining), 0)).where(and_(*clauses))
        result = await self.session.execute(stmt)
        return Decimal(str(result.scalar_one()))


def _expiry_status_expression(boundaries: ExpiryBoundaries) -> ColumnElement[str]:
    return case(
        (Batch.expires_at <= boundaries.today, "expired"),
        (Batch.expires_at <= boundaries.red_until, "red"),
        (Batch.expires_at <= boundaries.orange_until, "orange"),
        (Batch.expires_at <= boundaries.yellow_until, "yellow"),
        else_="normal",
    ).label("expiry_status")
