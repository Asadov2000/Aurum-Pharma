"""Database access for suppliers and supplier returns."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.catalog.models import TenantCatalog
from app.domains.foundation.models import Branch
from app.domains.incoming.models import IncomingDocument, IncomingItem
from app.domains.inventory.models import Batch
from app.domains.suppliers.models import Supplier, SupplierReturn


@dataclass(frozen=True, slots=True)
class SupplierSearchSummaryData:
    all_count: int
    active_count: int
    inactive_count: int
    with_contact_count: int


@dataclass(frozen=True, slots=True)
class SupplierReturnRow:
    supplier_return: SupplierReturn
    supplier_name: str
    branch_id: UUID
    branch_name: str
    batch_number: str | None
    catalog_name: str
    catalog_form: str | None
    catalog_dosage: str | None
    catalog_pack_size: str | None
    source_document_number: str | None


@dataclass(frozen=True, slots=True)
class SupplierReturnSummaryData:
    total: int
    total_qty: Decimal
    total_amount: Decimal


@dataclass(frozen=True, slots=True)
class SupplierReturnCandidateRow:
    batch: Batch
    source_document_id: UUID
    document_number: str | None
    document_date: date
    branch_name: str
    catalog_name: str
    catalog_form: str | None
    catalog_dosage: str | None
    catalog_pack_size: str | None


class SuppliersRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_supplier(self, **fields: Any) -> Supplier:
        supplier = Supplier(**fields)
        self.session.add(supplier)
        await self.session.flush()
        await self.session.refresh(supplier)
        return supplier

    async def get_supplier(self, supplier_id: UUID, *, tenant_id: UUID) -> Supplier | None:
        stmt = select(Supplier).where(
            Supplier.id == supplier_id,
            Supplier.tenant_id == tenant_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_suppliers(
        self,
        *,
        tenant_id: UUID,
        include_inactive: bool = False,
    ) -> list[Supplier]:
        clauses: list[ColumnElement[bool]] = [Supplier.tenant_id == tenant_id]
        if not include_inactive:
            clauses.append(Supplier.is_active.is_(True))
        stmt = (
            select(Supplier)
            .where(*clauses)
            .order_by(func.lower(Supplier.name).asc(), Supplier.id.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def search_suppliers(
        self,
        *,
        tenant_id: UUID,
        q: str | None = None,
        is_active: bool | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[Supplier], int, SupplierSearchSummaryData]:
        base_clauses: list[ColumnElement[bool]] = [Supplier.tenant_id == tenant_id]
        term = q.strip() if q is not None else ""
        if term:
            base_clauses.append(
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

        list_clauses = list(base_clauses)
        if is_active is not None:
            list_clauses.append(Supplier.is_active.is_(is_active))

        total = int(
            (
                await self.session.execute(
                    select(func.count()).select_from(Supplier).where(*list_clauses)
                )
            ).scalar_one()
        )
        stmt = (
            select(Supplier)
            .where(*list_clauses)
            .order_by(func.lower(Supplier.name).asc(), Supplier.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = list((await self.session.execute(stmt)).scalars().all())

        summary_row = (
            await self.session.execute(
                select(
                    func.count().label("all_count"),
                    func.count().filter(Supplier.is_active.is_(True)).label("active_count"),
                    func.count().filter(Supplier.is_active.is_(False)).label("inactive_count"),
                    func.count()
                    .filter(
                        or_(
                            func.nullif(func.btrim(Supplier.phone), "").is_not(None),
                            func.nullif(func.btrim(Supplier.email), "").is_not(None),
                        )
                    )
                    .label("with_contact_count"),
                )
                .select_from(Supplier)
                .where(*base_clauses)
            )
        ).one()
        return (
            items,
            total,
            SupplierSearchSummaryData(
                all_count=int(summary_row.all_count),
                active_count=int(summary_row.active_count),
                inactive_count=int(summary_row.inactive_count),
                with_contact_count=int(summary_row.with_contact_count),
            ),
        )

    async def search_supplier_options(
        self,
        *,
        tenant_id: UUID,
        q: str | None,
        include_inactive: bool,
        selected_id: UUID | None,
        limit: int,
    ) -> list[Supplier]:
        clauses: list[ColumnElement[bool]] = [Supplier.tenant_id == tenant_id]
        if not include_inactive:
            clauses.append(
                or_(Supplier.is_active.is_(True), Supplier.id == selected_id)
                if selected_id is not None
                else Supplier.is_active.is_(True)
            )
        if q:
            clauses.append(Supplier.name.icontains(q.strip(), autoescape=True))
        ordering = [func.lower(Supplier.name).asc(), Supplier.id.asc()]
        if selected_id is not None:
            ordering.insert(0, (Supplier.id == selected_id).desc())
        stmt = select(Supplier).where(*clauses).order_by(*ordering).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def update_supplier(self, supplier: Supplier, **fields: Any) -> Supplier:
        for key, value in fields.items():
            setattr(supplier, key, value)
        await self.session.flush()
        await self.session.refresh(supplier)
        return supplier

    async def insert_return(self, **fields: Any) -> SupplierReturn:
        supplier_return = SupplierReturn(**fields)
        self.session.add(supplier_return)
        await self.session.flush()
        await self.session.refresh(supplier_return)
        return supplier_return

    async def get_return(
        self,
        return_id: UUID,
        *,
        tenant_id: UUID,
    ) -> SupplierReturn | None:
        stmt = select(SupplierReturn).where(
            SupplierReturn.id == return_id,
            SupplierReturn.tenant_id == tenant_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_batch_for_update(self, batch_id: UUID, *, tenant_id: UUID) -> Batch | None:
        stmt = (
            select(Batch)
            .where(Batch.id == batch_id, Batch.tenant_id == tenant_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_batch_origin(
        self,
        batch_id: UUID,
        *,
        tenant_id: UUID,
    ) -> IncomingDocument | None:
        stmt = (
            select(IncomingDocument)
            .join(
                IncomingItem,
                and_(
                    IncomingItem.document_id == IncomingDocument.id,
                    IncomingItem.tenant_id == IncomingDocument.tenant_id,
                ),
            )
            .where(
                IncomingItem.created_batch_id == batch_id,
                IncomingItem.tenant_id == tenant_id,
                IncomingDocument.tenant_id == tenant_id,
                IncomingDocument.status == "accepted",
            )
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def search_return_candidates(
        self,
        *,
        tenant_id: UUID,
        supplier_id: UUID,
        branch_id: UUID | None,
        branch_ids: set[UUID] | None,
        q: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[SupplierReturnCandidateRow], int]:
        if branch_ids is not None and not branch_ids:
            return [], 0

        clauses: list[ColumnElement[bool]] = [
            IncomingDocument.tenant_id == tenant_id,
            IncomingDocument.supplier_id == supplier_id,
            IncomingDocument.status == "accepted",
            IncomingItem.tenant_id == tenant_id,
            IncomingItem.created_batch_id.is_not(None),
            Batch.tenant_id == tenant_id,
            Batch.qty_remaining > 0,
        ]
        if branch_id is not None:
            clauses.append(Batch.branch_id == branch_id)
        if branch_ids is not None:
            clauses.append(Batch.branch_id.in_(sorted(branch_ids, key=str)))
        if q:
            term = q.strip()
            clauses.append(
                or_(
                    TenantCatalog.brand_name.icontains(term, autoescape=True),
                    Batch.batch_number.icontains(term, autoescape=True),
                    IncomingDocument.document_number.icontains(term, autoescape=True),
                )
            )

        from_clause = (
            IncomingDocument.__table__.join(
                IncomingItem.__table__,
                and_(
                    IncomingItem.document_id == IncomingDocument.id,
                    IncomingItem.tenant_id == IncomingDocument.tenant_id,
                ),
            )
            .join(
                Batch.__table__,
                and_(
                    Batch.id == IncomingItem.created_batch_id,
                    Batch.tenant_id == IncomingItem.tenant_id,
                ),
            )
            .join(
                Branch.__table__,
                and_(Branch.id == Batch.branch_id, Branch.tenant_id == Batch.tenant_id),
            )
            .join(
                TenantCatalog.__table__,
                and_(
                    TenantCatalog.id == Batch.catalog_id,
                    TenantCatalog.tenant_id == Batch.tenant_id,
                ),
            )
        )
        total = int(
            (
                await self.session.execute(
                    select(func.count()).select_from(from_clause).where(*clauses)
                )
            ).scalar_one()
        )
        stmt = (
            select(
                Batch,
                IncomingDocument.id,
                IncomingDocument.document_number,
                IncomingDocument.document_date,
                Branch.name,
                TenantCatalog.brand_name,
                TenantCatalog.form,
                TenantCatalog.dosage,
                TenantCatalog.pack_size,
            )
            .select_from(from_clause)
            .where(*clauses)
            .order_by(Batch.expires_at.asc(), IncomingDocument.document_date.desc(), Batch.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            SupplierReturnCandidateRow(
                batch=batch,
                source_document_id=document_id,
                document_number=document_number,
                document_date=document_date,
                branch_name=branch_name,
                catalog_name=catalog_name,
                catalog_form=catalog_form,
                catalog_dosage=catalog_dosage,
                catalog_pack_size=catalog_pack_size,
            )
            for (
                batch,
                document_id,
                document_number,
                document_date,
                branch_name,
                catalog_name,
                catalog_form,
                catalog_dosage,
                catalog_pack_size,
            ) in rows
        ], total

    async def search_returns(
        self,
        *,
        tenant_id: UUID,
        supplier_id: UUID | None,
        branch_id: UUID | None,
        branch_ids: set[UUID] | None,
        reason: str | None,
        created_from: datetime | None,
        created_to: datetime | None,
        page: int,
        page_size: int,
    ) -> tuple[list[SupplierReturnRow], SupplierReturnSummaryData]:
        if branch_ids is not None and not branch_ids:
            return [], SupplierReturnSummaryData(0, Decimal("0"), Decimal("0"))

        clauses: list[ColumnElement[bool]] = [SupplierReturn.tenant_id == tenant_id]
        if supplier_id is not None:
            clauses.append(SupplierReturn.supplier_id == supplier_id)
        if branch_id is not None:
            clauses.append(Batch.branch_id == branch_id)
        if branch_ids is not None:
            clauses.append(Batch.branch_id.in_(sorted(branch_ids, key=str)))
        if reason is not None:
            clauses.append(SupplierReturn.reason == reason)
        if created_from is not None:
            clauses.append(SupplierReturn.created_at >= created_from)
        if created_to is not None:
            clauses.append(SupplierReturn.created_at < created_to)

        join_condition = and_(Batch.id == SupplierReturn.batch_id, Batch.tenant_id == tenant_id)
        from_clause = (
            SupplierReturn.__table__.join(Batch.__table__, join_condition)
            .join(
                Supplier.__table__,
                and_(
                    Supplier.id == SupplierReturn.supplier_id,
                    Supplier.tenant_id == SupplierReturn.tenant_id,
                ),
            )
            .join(
                Branch.__table__,
                and_(Branch.id == Batch.branch_id, Branch.tenant_id == Batch.tenant_id),
            )
            .join(
                TenantCatalog.__table__,
                and_(
                    TenantCatalog.id == Batch.catalog_id,
                    TenantCatalog.tenant_id == Batch.tenant_id,
                ),
            )
            .outerjoin(
                IncomingDocument.__table__,
                and_(
                    IncomingDocument.id == SupplierReturn.source_document_id,
                    IncomingDocument.tenant_id == SupplierReturn.tenant_id,
                ),
            )
        )
        summary_row = (
            await self.session.execute(
                select(
                    func.count().label("total"),
                    func.coalesce(func.sum(SupplierReturn.qty), 0).label("total_qty"),
                    func.coalesce(func.sum(SupplierReturn.amount), 0).label("total_amount"),
                )
                .select_from(from_clause)
                .where(*clauses)
            )
        ).one()
        stmt = (
            select(
                SupplierReturn,
                Supplier.name,
                Batch.branch_id,
                Branch.name,
                Batch.batch_number,
                TenantCatalog.brand_name,
                TenantCatalog.form,
                TenantCatalog.dosage,
                TenantCatalog.pack_size,
                IncomingDocument.document_number,
            )
            .select_from(from_clause)
            .where(*clauses)
            .order_by(SupplierReturn.created_at.desc(), SupplierReturn.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            SupplierReturnRow(
                supplier_return=supplier_return,
                supplier_name=supplier_name,
                branch_id=resolved_branch_id,
                branch_name=branch_name,
                batch_number=batch_number,
                catalog_name=catalog_name,
                catalog_form=catalog_form,
                catalog_dosage=catalog_dosage,
                catalog_pack_size=catalog_pack_size,
                source_document_number=document_number,
            )
            for (
                supplier_return,
                supplier_name,
                resolved_branch_id,
                branch_name,
                batch_number,
                catalog_name,
                catalog_form,
                catalog_dosage,
                catalog_pack_size,
                document_number,
            ) in rows
        ], SupplierReturnSummaryData(
            total=int(summary_row.total),
            total_qty=Decimal(str(summary_row.total_qty)),
            total_amount=Decimal(str(summary_row.total_amount)),
        )
