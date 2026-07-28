"""DB access for the suppliers domain."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.inventory.models import Batch
from app.domains.suppliers.models import Supplier, SupplierReturn


class SuppliersRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -------- supplier --------

    async def create_supplier(self, **fields: Any) -> Supplier:
        s = Supplier(**fields)
        self.session.add(s)
        await self.session.flush()
        await self.session.refresh(s)
        return s

    async def get_supplier(self, supplier_id: UUID) -> Supplier | None:
        return await self.session.get(Supplier, supplier_id)

    async def list_suppliers(self, *, include_inactive: bool = False) -> list[Supplier]:
        stmt = select(Supplier)
        if not include_inactive:
            stmt = stmt.where(Supplier.is_active.is_(True))
        stmt = stmt.order_by(Supplier.name.asc(), Supplier.id.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def search_suppliers(
        self,
        *,
        tenant_id: UUID,
        q: str | None = None,
        is_active: bool | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[Supplier], int]:
        clauses: list[ColumnElement[bool]] = [Supplier.tenant_id == tenant_id]
        term = q.strip() if q is not None else ""
        if term:
            clauses.append(
                or_(
                    Supplier.name.icontains(term, autoescape=True),
                    Supplier.legal_name.icontains(term, autoescape=True),
                    Supplier.inn_or_tin.icontains(term, autoescape=True),
                    Supplier.contact_person.icontains(term, autoescape=True),
                    Supplier.phone.icontains(term, autoescape=True),
                    Supplier.email.icontains(term, autoescape=True),
                    Supplier.address.icontains(term, autoescape=True),
                )
            )
        if is_active is not None:
            clauses.append(Supplier.is_active.is_(is_active))

        count_stmt = select(func.count()).select_from(Supplier).where(*clauses)
        total = int((await self.session.execute(count_stmt)).scalar_one())
        stmt = (
            select(Supplier)
            .where(*clauses)
            .order_by(func.lower(Supplier.name).asc(), Supplier.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def update_supplier(self, supplier: Supplier, **fields: Any) -> Supplier:
        for k, v in fields.items():
            setattr(supplier, k, v)
        await self.session.flush()
        await self.session.refresh(supplier)
        return supplier

    # -------- supplier_return --------

    async def insert_return(self, **fields: Any) -> SupplierReturn:
        sr = SupplierReturn(**fields)
        self.session.add(sr)
        await self.session.flush()
        await self.session.refresh(sr)
        return sr

    async def list_returns(
        self,
        *,
        supplier_id: UUID | None = None,
        date_from: date | datetime | None = None,
        date_to: date | datetime | None = None,
        branch_ids: set[UUID] | None = None,
    ) -> list[SupplierReturn]:
        stmt = select(SupplierReturn)
        clauses: list[Any] = []
        if branch_ids is not None:
            if not branch_ids:
                return []
            stmt = stmt.join(Batch, Batch.id == SupplierReturn.batch_id)
            clauses.append(Batch.branch_id.in_(branch_ids))
        if supplier_id is not None:
            clauses.append(SupplierReturn.supplier_id == supplier_id)
        if date_from is not None:
            clauses.append(SupplierReturn.created_at >= date_from)
        if date_to is not None:
            clauses.append(SupplierReturn.created_at <= date_to)
        if clauses:
            stmt = stmt.where(and_(*clauses))
        stmt = stmt.order_by(SupplierReturn.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
