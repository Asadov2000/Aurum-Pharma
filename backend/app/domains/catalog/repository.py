"""Database access for the catalog domain."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, exists, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.domains.catalog.models import Barcode, CatalogImportJob, TenantCatalog
from app.domains.inventory.models import Batch


class CatalogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @asynccontextmanager
    async def row_savepoint(self) -> AsyncIterator[None]:
        """Keep one import row failure from aborting the whole file transaction."""
        async with self.session.begin_nested():
            yield

    async def stock_by_catalog(
        self, *, branch_id: UUID, catalog_ids: list[UUID]
    ) -> dict[UUID, Decimal]:
        """Available stock per catalog item at one branch, in a single grouped,
        sargable query (filters on indexed columns, no functions, no N+1).
        Mirrors the page of search results passed in `catalog_ids`."""
        if not catalog_ids:
            return {}
        stmt = (
            select(Batch.catalog_id, func.coalesce(func.sum(Batch.qty_remaining), 0))
            .where(
                Batch.branch_id == branch_id,
                Batch.is_blocked.is_(False),
                Batch.expires_at > date.today(),
                Batch.catalog_id.in_(catalog_ids),
            )
            .group_by(Batch.catalog_id)
        )
        rows = (await self.session.execute(stmt)).all()
        return {cid: Decimal(str(total)) for cid, total in rows}

    # -------------------------------------------------------------------------
    # tenant_catalog
    # -------------------------------------------------------------------------

    async def create_item(self, **fields: Any) -> TenantCatalog:
        item = TenantCatalog(**fields)
        self.session.add(item)
        await self.session.flush()
        await self.session.refresh(item)
        return item

    async def get_item(
        self, item_id: UUID, *, include_deleted: bool = False
    ) -> TenantCatalog | None:
        item = await self.session.get(TenantCatalog, item_id)
        if item is None:
            return None
        if not include_deleted and item.deleted_at is not None:
            return None
        return item

    async def update_item(self, item: TenantCatalog, **fields: Any) -> TenantCatalog:
        for key, value in fields.items():
            setattr(item, key, value)
        await self.session.flush()
        await self.session.refresh(item)
        return item

    async def soft_delete_item(self, item_id: UUID) -> int:
        result = await self.session.execute(
            update(TenantCatalog)
            .where(and_(TenantCatalog.id == item_id, TenantCatalog.deleted_at.is_(None)))
            .values(deleted_at=utc_now(), is_active=False)
        )
        return result.rowcount or 0  # type: ignore[attr-defined]

    async def restore_item(self, item_id: UUID) -> TenantCatalog | None:
        item = await self.get_item(item_id, include_deleted=True)
        if item is None or item.deleted_at is None:
            return None
        return await self.update_item(item, deleted_at=None, is_active=True)

    async def search(
        self,
        *,
        q: str | None,
        category: str | None,
        dispensing_type: str | None,
        page: int,
        page_size: int,
        manufacturer: str | None = None,
        storage_type: str | None = None,
        lifecycle: str = "active",
    ) -> tuple[list[TenantCatalog], int]:
        """Trigram search across brand_name, inn, and manufacturer. Filters by category and
        dispensing_type are exact-match. Tenant scoping is handled by RLS;
        we still add deleted_at IS NULL because the policy doesn't filter
        soft-deletes."""

        filters: list[Any] = []
        if lifecycle == "active":
            filters.extend([TenantCatalog.deleted_at.is_(None), TenantCatalog.is_active.is_(True)])
        elif lifecycle == "inactive":
            filters.extend([TenantCatalog.deleted_at.is_(None), TenantCatalog.is_active.is_(False)])
        elif lifecycle == "archived":
            filters.append(TenantCatalog.deleted_at.is_not(None))
        if category:
            filters.append(TenantCatalog.category == category)
        if dispensing_type:
            filters.append(TenantCatalog.dispensing_type == dispensing_type)
        if manufacturer:
            filters.append(TenantCatalog.manufacturer == manufacturer.strip())
        if storage_type:
            filters.append(TenantCatalog.storage_type == storage_type)

        base_stmt = select(TenantCatalog).where(*filters)
        if q:
            # pg_trgm `%` operator with similarity_threshold defaulting to 0.3.
            base_stmt = base_stmt.where(
                or_(
                    text("(brand_name % :q OR inn % :q OR manufacturer % :q)").bindparams(q=q),
                    exists(
                        select(1).where(
                            Barcode.catalog_id == TenantCatalog.id,
                            Barcode.code == q,
                        )
                    ),
                )
            )

        count_stmt = select(func.count()).select_from(TenantCatalog).where(*filters)
        if q:
            count_stmt = count_stmt.where(
                or_(
                    text("(brand_name % :q OR inn % :q OR manufacturer % :q)").bindparams(q=q),
                    exists(
                        select(1).where(
                            Barcode.catalog_id == TenantCatalog.id,
                            Barcode.code == q,
                        )
                    ),
                )
            )

        total = int((await self.session.execute(count_stmt)).scalar_one())

        list_stmt = (
            base_stmt.order_by(TenantCatalog.brand_name.asc(), TenantCatalog.id.asc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        result = await self.session.execute(list_stmt)
        return list(result.scalars().all()), total

    async def find_duplicate(
        self,
        *,
        tenant_id: UUID,
        brand_name: str,
        manufacturer: str | None,
        dosage: str | None,
        pack_size: str | None,
    ) -> TenantCatalog | None:
        """Duplicate key per the import spec: brand_name + manufacturer +
        dosage + pack_size, case-insensitive on the name."""
        stmt = (
            select(TenantCatalog)
            .where(
                and_(
                    TenantCatalog.tenant_id == tenant_id,
                    TenantCatalog.deleted_at.is_(None),
                    func.lower(TenantCatalog.brand_name) == brand_name.lower(),
                    (
                        TenantCatalog.manufacturer.is_(manufacturer)
                        if manufacturer is None
                        else TenantCatalog.manufacturer == manufacturer
                    ),
                    (
                        TenantCatalog.dosage.is_(dosage)
                        if dosage is None
                        else TenantCatalog.dosage == dosage
                    ),
                    (
                        TenantCatalog.pack_size.is_(pack_size)
                        if pack_size is None
                        else TenantCatalog.pack_size == pack_size
                    ),
                )
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def soft_delete_by_import_job(self, import_job_id: UUID, *, when: datetime) -> int:
        """Rollback helper — soft-deletes every row a job inserted."""
        result = await self.session.execute(
            update(TenantCatalog)
            .where(
                and_(
                    TenantCatalog.import_job_id == import_job_id,
                    TenantCatalog.deleted_at.is_(None),
                )
            )
            .values(deleted_at=when, is_active=False)
        )
        return result.rowcount or 0  # type: ignore[attr-defined]

    # -------------------------------------------------------------------------
    # barcode
    # -------------------------------------------------------------------------

    async def add_barcode(self, **fields: Any) -> Barcode:
        bc = Barcode(**fields)
        self.session.add(bc)
        await self.session.flush()
        await self.session.refresh(bc)
        return bc

    async def list_barcodes_for_item(self, catalog_id: UUID) -> list[Barcode]:
        stmt = select(Barcode).where(Barcode.catalog_id == catalog_id).order_by(Barcode.code)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def find_item_by_barcode(self, code: str) -> TenantCatalog | None:
        stmt = (
            select(TenantCatalog)
            .join(Barcode, Barcode.catalog_id == TenantCatalog.id)
            .where(
                and_(
                    Barcode.code == code,
                    TenantCatalog.deleted_at.is_(None),
                )
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def delete_barcode(self, barcode_id: UUID, *, catalog_id: UUID) -> int:
        result = await self.session.execute(
            delete(Barcode).where(and_(Barcode.id == barcode_id, Barcode.catalog_id == catalog_id))
        )
        return result.rowcount or 0  # type: ignore[attr-defined]

    # -------------------------------------------------------------------------
    # catalog_import_job
    # -------------------------------------------------------------------------

    async def create_job(self, **fields: Any) -> CatalogImportJob:
        job = CatalogImportJob(**fields)
        self.session.add(job)
        await self.session.flush()
        await self.session.refresh(job)
        return job

    async def get_job(self, job_id: UUID) -> CatalogImportJob | None:
        return await self.session.get(CatalogImportJob, job_id)

    async def update_job(self, job: CatalogImportJob, **fields: Any) -> CatalogImportJob:
        for key, value in fields.items():
            setattr(job, key, value)
        await self.session.flush()
        await self.session.refresh(job)
        return job
