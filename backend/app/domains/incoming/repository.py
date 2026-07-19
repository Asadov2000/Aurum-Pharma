"""DB access for the incoming domain."""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.incoming.models import IncomingDocument, IncomingItem


class IncomingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- documents ----

    async def create_document(self, **fields: Any) -> IncomingDocument:
        d = IncomingDocument(**fields)
        self.session.add(d)
        await self.session.flush()
        await self.session.refresh(d)
        return d

    async def get_document(self, document_id: UUID) -> IncomingDocument | None:
        return await self.session.get(IncomingDocument, document_id)

    async def list_documents(
        self,
        *,
        branch_id: UUID | None = None,
        branch_ids: set[UUID] | None = None,
        supplier_id: UUID | None = None,
        status: str | None = None,
        document_number: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[IncomingDocument], int]:
        if branch_ids is not None and not branch_ids:
            return [], 0

        clauses: list[Any] = []
        if branch_id is not None:
            clauses.append(IncomingDocument.branch_id == branch_id)
        if branch_ids is not None:
            clauses.append(IncomingDocument.branch_id.in_(sorted(branch_ids, key=str)))
        if supplier_id is not None:
            clauses.append(IncomingDocument.supplier_id == supplier_id)
        if status is not None:
            clauses.append(IncomingDocument.status == status)
        if document_number:
            clauses.append(
                IncomingDocument.document_number.icontains(
                    document_number.strip(),
                    autoescape=True,
                )
            )
        if date_from is not None:
            clauses.append(IncomingDocument.document_date >= date_from)
        if date_to is not None:
            clauses.append(IncomingDocument.document_date <= date_to)

        count_stmt = select(func.count()).select_from(IncomingDocument)
        stmt = select(IncomingDocument)
        if clauses:
            count_stmt = count_stmt.where(and_(*clauses))
            stmt = stmt.where(and_(*clauses))

        total = int((await self.session.execute(count_stmt)).scalar_one())
        stmt = (
            stmt.order_by(
                IncomingDocument.document_date.desc(),
                IncomingDocument.created_at.desc(),
                IncomingDocument.id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def update_document(self, doc: IncomingDocument, **fields: Any) -> IncomingDocument:
        for k, v in fields.items():
            setattr(doc, k, v)
        await self.session.flush()
        await self.session.refresh(doc)
        return doc

    # ---- items ----

    async def create_item(self, **fields: Any) -> IncomingItem:
        item = IncomingItem(**fields)
        self.session.add(item)
        await self.session.flush()
        await self.session.refresh(item)
        return item

    async def get_item(self, item_id: UUID) -> IncomingItem | None:
        return await self.session.get(IncomingItem, item_id)

    async def list_items(self, document_id: UUID) -> list[IncomingItem]:
        stmt = (
            select(IncomingItem)
            .where(IncomingItem.document_id == document_id)
            .order_by(IncomingItem.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update_item(self, item: IncomingItem, **fields: Any) -> IncomingItem:
        for k, v in fields.items():
            setattr(item, k, v)
        await self.session.flush()
        await self.session.refresh(item)
        return item

    async def delete_item(self, item_id: UUID, document_id: UUID) -> int:
        result = await self.session.execute(
            delete(IncomingItem).where(
                and_(IncomingItem.id == item_id, IncomingItem.document_id == document_id)
            )
        )
        return result.rowcount or 0  # type: ignore[attr-defined]

    async def total_amount(self, document_id: UUID) -> float:
        stmt = select(
            func.coalesce(func.sum(IncomingItem.qty * IncomingItem.purchase_price), 0)
        ).where(IncomingItem.document_id == document_id)
        result = await self.session.execute(stmt)
        return float(result.scalar_one())
