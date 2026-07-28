"""Database access for the inventory domain.

The v_batch_with_expiry_status view is queried via raw text() rows
(SELECT *, expiry_status, days_to_expiry FROM ...) because there's no
ORM mapping for views — the result is materialised by the service into
BatchWithExpiry dicts.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory.models import Batch, BatchMovement, WriteOff


class InventoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -------------------------------------------------------------------------
    # batch (read)
    # -------------------------------------------------------------------------

    async def get_batch(self, batch_id: UUID) -> Batch | None:
        return await self.session.get(Batch, batch_id)

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
        show_empty: bool,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """Returns (rows, total). Rows include the expiry_status column from
        v_batch_with_expiry_status. show_empty=True falls back to plain
        batch table (the view skips qty_remaining = 0)."""
        expiry_case = (
            "CASE "
            "WHEN b.expires_at <= CURRENT_DATE THEN 'expired' "
            "WHEN b.expires_at <= CURRENT_DATE + INTERVAL '1 month' THEN 'red' "
            "WHEN b.expires_at <= CURRENT_DATE + INTERVAL '3 months' THEN 'orange' "
            "WHEN b.expires_at <= CURRENT_DATE + INTERVAL '6 months' THEN 'yellow' "
            "ELSE 'normal' END"
        )
        if show_empty:
            base = "FROM batch b WHERE 1=1"
            extra_cols = (
                f", {expiry_case} AS expiry_status, "
                "(b.expires_at - CURRENT_DATE) AS days_to_expiry"
            )
        else:
            base = "FROM v_batch_with_expiry_status b WHERE 1=1"
            extra_cols = ""

        clauses: list[str] = []
        params: dict[str, Any] = {}
        if catalog_id is not None:
            clauses.append("AND b.catalog_id = :catalog_id")
            params["catalog_id"] = catalog_id
        if branch_id is not None:
            clauses.append("AND b.branch_id = :branch_id")
            params["branch_id"] = branch_id
        if branch_ids is not None:
            if not branch_ids:
                clauses.append("AND 1 = 0")
            else:
                branch_keys: list[str] = []
                for idx, allowed_branch_id in enumerate(sorted(branch_ids, key=str)):
                    key = f"allowed_branch_{idx}"
                    branch_keys.append(f":{key}")
                    params[key] = allowed_branch_id
                clauses.append(f"AND b.branch_id IN ({', '.join(branch_keys)})")
        if expiry_status:
            expiry_filter = expiry_case if show_empty else "b.expiry_status"
            clauses.append(f"AND ({expiry_filter}) = :expiry_status")
            params["expiry_status"] = expiry_status

        where = " ".join(clauses)
        list_sql = (
            f"SELECT b.* {extra_cols} {base} {where} "
            f"ORDER BY b.expires_at ASC, b.id ASC LIMIT :limit OFFSET :offset"
        )
        count_sql = (
            f"SELECT COUNT(*) {base} {where}"
            if show_empty or not expiry_status
            else f"SELECT COUNT(*) FROM v_batch_with_expiry_status b WHERE 1=1 {where}"
        )

        list_params = {**params, "limit": page_size, "offset": (page - 1) * page_size}
        rows_result = await self.session.execute(text(list_sql), list_params)
        rows = [dict(row._mapping) for row in rows_result.fetchall()]
        total = int((await self.session.execute(text(count_sql), params)).scalar_one())
        return rows, total

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

    # -------------------------------------------------------------------------
    # FEFO selection
    # -------------------------------------------------------------------------

    async def fefo_candidates(
        self,
        *,
        catalog_id: UUID,
        branch_id: UUID,
        include_expired: bool,
        today: date,
    ) -> list[Batch]:
        """All non-blocked, non-empty batches for catalog+branch, sorted by
        expires_at ASC. If include_expired=False, filters expired ones out."""
        stmt = (
            select(Batch)
            .where(
                and_(
                    Batch.catalog_id == catalog_id,
                    Batch.branch_id == branch_id,
                    Batch.qty_remaining > 0,
                    Batch.is_blocked.is_(False),
                )
            )
            .order_by(Batch.expires_at.asc(), Batch.created_at.asc())
        )
        if not include_expired:
            stmt = stmt.where(Batch.expires_at > today)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def sum_qty_remaining(self, *, catalog_id: UUID, branch_id: UUID) -> Decimal:
        stmt = select(func.coalesce(func.sum(Batch.qty_remaining), 0)).where(
            and_(
                Batch.catalog_id == catalog_id,
                Batch.branch_id == branch_id,
                Batch.is_blocked.is_(False),
            )
        )
        result = await self.session.execute(stmt)
        return Decimal(str(result.scalar_one()))
