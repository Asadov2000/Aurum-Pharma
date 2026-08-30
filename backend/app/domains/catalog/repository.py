"""Database access for the catalog domain."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, delete, exists, func, not_, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.domains.catalog.models import Barcode, CatalogImportJob, TenantCatalog
from app.domains.inventory.models import Batch


def _normalize_search_text(value: str) -> str:
    return " ".join(value.split())


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _contains_pattern(value: str) -> str:
    escaped = _escape_like(value)
    return f"%{escaped}%"


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

    async def get_item_for_update(self, item_id: UUID) -> TenantCatalog | None:
        stmt = select(TenantCatalog).where(TenantCatalog.id == item_id).with_for_update()
        item = (await self.session.execute(stmt)).scalar_one_or_none()
        if item is None or item.deleted_at is not None:
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

    async def search(  # noqa: PLR0912 - each branch is an explicit catalog filter state
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
        image_state: str = "any",
        barcode_state: str = "any",
    ) -> tuple[list[TenantCatalog], int]:
        """Search brand, INN, manufacturer, and barcode with relevance ordering.

        Category and manufacturer support case-insensitive partial matching; enum filters
        remain exact. Tenant scoping is handled by RLS;
        we still add deleted_at IS NULL because the policy doesn't filter
        soft-deletes."""

        filters: list[Any] = []
        if lifecycle == "active":
            filters.extend([TenantCatalog.deleted_at.is_(None), TenantCatalog.is_active.is_(True)])
        elif lifecycle == "inactive":
            filters.extend([TenantCatalog.deleted_at.is_(None), TenantCatalog.is_active.is_(False)])
        elif lifecycle == "archived":
            filters.append(TenantCatalog.deleted_at.is_not(None))
        elif lifecycle == "current":
            filters.append(TenantCatalog.deleted_at.is_(None))
        normalized_category = _normalize_search_text(category) if category else ""
        if normalized_category:
            filters.append(
                TenantCatalog.category.ilike(
                    _contains_pattern(normalized_category), escape="\\"
                )
            )
        if dispensing_type:
            filters.append(TenantCatalog.dispensing_type == dispensing_type)
        normalized_manufacturer = _normalize_search_text(manufacturer) if manufacturer else ""
        if normalized_manufacturer:
            filters.append(
                TenantCatalog.manufacturer.ilike(
                    _contains_pattern(normalized_manufacturer), escape="\\"
                )
            )
        if storage_type:
            filters.append(TenantCatalog.storage_type == storage_type)
        if image_state == "with_image":
            filters.append(TenantCatalog.image_version.is_not(None))
        elif image_state == "without_image":
            filters.append(TenantCatalog.image_version.is_(None))

        barcode_exists = exists(select(1).where(Barcode.catalog_id == TenantCatalog.id))
        if barcode_state == "with_barcode":
            filters.append(barcode_exists)
        elif barcode_state == "without_barcode":
            filters.append(not_(barcode_exists))

        relevance_order: list[Any] = []
        normalized = _normalize_search_text(q) if q else ""
        if normalized:
            contains_pattern = _contains_pattern(normalized)
            escaped = _escape_like(normalized)
            prefix_pattern = f"{escaped}%"
            barcode_match = exists(
                select(1).where(
                    Barcode.catalog_id == TenantCatalog.id,
                    Barcode.code == normalized,
                )
            )
            # pg_trgm `%` operator with similarity_threshold defaulting to 0.3.
            text_match = or_(
                TenantCatalog.brand_name.ilike(contains_pattern, escape="\\"),
                TenantCatalog.inn.ilike(contains_pattern, escape="\\"),
                TenantCatalog.manufacturer.ilike(contains_pattern, escape="\\"),
                text(
                    "(brand_name % :catalog_q OR inn % :catalog_q "
                    "OR manufacturer % :catalog_q)"
                ).bindparams(catalog_q=normalized),
            )
            filters.append(or_(barcode_match, text_match))
            relevance_order = [
                barcode_match.desc(),
                case(
                    (func.lower(TenantCatalog.brand_name) == normalized.lower(), 0),
                    (TenantCatalog.brand_name.ilike(prefix_pattern, escape="\\"), 1),
                    (func.lower(TenantCatalog.inn) == normalized.lower(), 2),
                    (TenantCatalog.inn.ilike(prefix_pattern, escape="\\"), 3),
                    (TenantCatalog.brand_name.ilike(contains_pattern, escape="\\"), 4),
                    else_=5,
                ).asc(),
            ]

        base_stmt = select(TenantCatalog).where(*filters)
        count_stmt = select(func.count()).select_from(TenantCatalog).where(*filters)

        total = int((await self.session.execute(count_stmt)).scalar_one())

        list_stmt = (
            base_stmt.order_by(
                *relevance_order,
                TenantCatalog.brand_name.asc(),
                TenantCatalog.id.asc(),
            )
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        result = await self.session.execute(list_stmt)
        return list(result.scalars().all()), total

    async def search_picker(self, *, q: str, limit: int) -> list[TenantCatalog]:
        """Return a small relevance-ranked result set without a full COUNT query."""

        normalized = _normalize_search_text(q)
        if not normalized:
            return []
        escaped = _escape_like(normalized)
        contains_pattern = f"%{escaped}%"
        prefix_pattern = f"{escaped}%"
        barcode_match = exists(
            select(1).where(
                Barcode.catalog_id == TenantCatalog.id,
                Barcode.code == normalized,
            )
        )
        text_match = or_(
            TenantCatalog.brand_name.ilike(contains_pattern, escape="\\"),
            TenantCatalog.inn.ilike(contains_pattern, escape="\\"),
            TenantCatalog.manufacturer.ilike(contains_pattern, escape="\\"),
            text(
                "(brand_name % :picker_q OR inn % :picker_q " "OR manufacturer % :picker_q)"
            ).bindparams(picker_q=normalized),
        )
        relevance_group = case(
            (func.lower(TenantCatalog.brand_name) == normalized.lower(), 0),
            (TenantCatalog.brand_name.ilike(prefix_pattern, escape="\\"), 1),
            (func.lower(TenantCatalog.inn) == normalized.lower(), 2),
            (TenantCatalog.inn.ilike(prefix_pattern, escape="\\"), 3),
            else_=4,
        )
        stmt = (
            select(TenantCatalog)
            .where(
                TenantCatalog.deleted_at.is_(None),
                TenantCatalog.is_active.is_(True),
                or_(barcode_match, text_match),
            )
            .order_by(
                barcode_match.desc(),
                relevance_group.asc(),
                TenantCatalog.brand_name.asc(),
                TenantCatalog.id.asc(),
            )
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def summary(self) -> dict[str, int]:
        alive = TenantCatalog.deleted_at.is_(None)
        barcode_exists = exists(select(1).where(Barcode.catalog_id == TenantCatalog.id))
        stmt = select(
            func.count(TenantCatalog.id).filter(alive).label("total"),
            func.count(TenantCatalog.id)
            .filter(alive, TenantCatalog.is_active.is_(True))
            .label("active"),
            func.count(TenantCatalog.id)
            .filter(alive, TenantCatalog.is_active.is_(False))
            .label("inactive"),
            func.count(TenantCatalog.id)
            .filter(TenantCatalog.deleted_at.is_not(None))
            .label("archived"),
            func.count(TenantCatalog.id)
            .filter(alive, not_(barcode_exists))
            .label("without_barcode"),
            func.count(TenantCatalog.id)
            .filter(alive, TenantCatalog.image_version.is_(None))
            .label("without_image"),
        )
        row = (await self.session.execute(stmt)).one()
        return {key: int(getattr(row, key) or 0) for key in row._fields}

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
