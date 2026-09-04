"""Business logic for the catalog domain.

CRUD on tenant_catalog + barcode operations, plus the import pipeline:
upload → preview (dry-run, first 100 parsed rows) → confirm (Celery) →
status → rollback (24 h after finished_at).

`process_import` is called by the Celery task `import_catalog_job`; it is
also reachable directly from tests for deterministic exercise of the
duplicate-handling branches.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog

from app.core.errors import (
    AurumError,
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.time import utc_now
from app.domains.catalog.image_processing import CatalogImageVariants
from app.domains.catalog.import_parser import parse_import
from app.domains.catalog.models import (
    Barcode,
    CatalogImportJob,
    TenantCatalog,
)
from app.domains.catalog.repository import CatalogRepository
from app.domains.foundation.repository import FoundationRepository

logger = structlog.get_logger("catalog.service")

PREVIEW_ROW_LIMIT = 100
ROLLBACK_WINDOW = timedelta(hours=24)


class CatalogService:
    def __init__(
        self,
        repo: CatalogRepository,
        *,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.repo = repo
        self._now = now

    async def _local_today(self, tenant_id: UUID) -> date:
        settings = await FoundationRepository(self.repo.session).get_settings(tenant_id)
        timezone_name = settings.report_timezone if settings is not None else "Asia/Dushanbe"
        try:
            return self._now().astimezone(ZoneInfo(timezone_name)).date()
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise AurumError("Tenant report timezone is invalid") from exc

    # -------------------------------------------------------------------------
    # CRUD
    # -------------------------------------------------------------------------

    async def create_item(
        self,
        *,
        tenant_id: UUID,
        fields: dict[str, Any],
        created_by: UUID | None = None,
    ) -> TenantCatalog:
        payload = {**fields, "tenant_id": tenant_id}
        if created_by is not None:
            payload["created_by"] = created_by
        return await self.repo.create_item(**payload)

    async def get_item(self, item_id: UUID, *, include_deleted: bool = False) -> TenantCatalog:
        item = await self.repo.get_item(item_id, include_deleted=include_deleted)
        if item is None:
            raise NotFoundError("Catalog item not found")
        return item

    async def get_item_with_barcodes(
        self, item_id: UUID, *, include_deleted: bool = False
    ) -> tuple[TenantCatalog, list[Barcode]]:
        item = await self.get_item(item_id, include_deleted=include_deleted)
        barcodes = await self.repo.list_barcodes_for_item(item.id)
        return item, barcodes

    async def update_item(
        self,
        item_id: UUID,
        *,
        fields: dict[str, Any],
        updated_by: UUID | None = None,
    ) -> TenantCatalog:
        item = await self.get_item(item_id)
        if updated_by is not None:
            fields = {**fields, "updated_by": updated_by}
        return await self.repo.update_item(item, **fields)

    async def soft_delete_item(self, item_id: UUID) -> None:
        rows = await self.repo.soft_delete_item(item_id)
        if rows == 0:
            raise NotFoundError("Catalog item not found")

    async def restore_item(self, item_id: UUID) -> TenantCatalog:
        item = await self.repo.restore_item(item_id)
        if item is None:
            raise NotFoundError("Archived catalog item not found")
        return item

    async def search(
        self,
        *,
        q: str | None,
        category: str | None,
        dispensing_type: str | None,
        page: int,
        page_size: int,
        branch_id: UUID | None = None,
        manufacturer: str | None = None,
        storage_type: str | None = None,
        lifecycle: str = "active",
        image_state: str = "any",
        barcode_state: str = "any",
        tenant_id: UUID,
    ) -> tuple[list[TenantCatalog], int, dict[UUID, Decimal]]:
        items, total = await self.repo.search(
            q=q,
            category=category,
            dispensing_type=dispensing_type,
            page=page,
            page_size=page_size,
            manufacturer=manufacturer,
            storage_type=storage_type,
            lifecycle=lifecycle,
            image_state=image_state,
            barcode_state=barcode_state,
            tenant_id=tenant_id,
        )
        # Available stock per result is computed only when a branch is given
        # (POS), in one grouped query over this page — never per-item (no N+1).
        stock: dict[UUID, Decimal] = {}
        if branch_id is not None and items:
            stock = await self.repo.stock_by_catalog(
                tenant_id=tenant_id,
                branch_id=branch_id,
                catalog_ids=[i.id for i in items],
                today=await self._local_today(tenant_id),
            )
        return items, total, stock

    async def search_picker(
        self,
        *,
        q: str,
        branch_id: UUID | None,
        limit: int,
        tenant_id: UUID,
    ) -> tuple[list[TenantCatalog], dict[UUID, Decimal]]:
        items = await self.repo.search_picker(q=q, limit=limit, tenant_id=tenant_id)
        stock: dict[UUID, Decimal] = {}
        if branch_id is not None and items:
            stock = await self.repo.stock_by_catalog(
                tenant_id=tenant_id,
                branch_id=branch_id,
                catalog_ids=[item.id for item in items],
                today=await self._local_today(tenant_id),
            )
        return items, stock

    async def summary(self) -> dict[str, int]:
        return await self.repo.summary()

    async def set_image_metadata(
        self,
        item_id: UUID,
        *,
        version: UUID,
        image: CatalogImageVariants,
        updated_by: UUID,
    ) -> tuple[TenantCatalog, UUID | None]:
        item = await self.repo.get_item_for_update(item_id)
        if item is None:
            raise NotFoundError("Catalog item not found")
        old_version = item.image_version
        updated = await self.repo.update_item(
            item,
            image_version=version,
            image_width=image.width,
            image_height=image.height,
            image_size_bytes=len(image.display),
            image_thumbnail_size_bytes=len(image.thumbnail),
            image_sha256=image.sha256,
            image_uploaded_at=utc_now(),
            image_uploaded_by=updated_by,
            updated_by=updated_by,
        )
        return updated, old_version

    async def clear_image_metadata(
        self, item_id: UUID, *, updated_by: UUID
    ) -> tuple[TenantCatalog, UUID | None]:
        item = await self.repo.get_item_for_update(item_id)
        if item is None:
            raise NotFoundError("Catalog item not found")
        old_version = item.image_version
        if old_version is None:
            return item, None
        updated = await self.repo.update_item(
            item,
            image_version=None,
            image_width=None,
            image_height=None,
            image_size_bytes=None,
            image_thumbnail_size_bytes=None,
            image_sha256=None,
            image_uploaded_at=None,
            image_uploaded_by=None,
            updated_by=updated_by,
        )
        return updated, old_version

    # -------------------------------------------------------------------------
    # Barcodes
    # -------------------------------------------------------------------------

    async def add_barcode(
        self,
        *,
        tenant_id: UUID,
        catalog_id: UUID,
        code: str,
        code_type: str,
    ) -> Barcode:
        # Make sure the parent item exists and is alive.
        item = await self.repo.get_item(catalog_id)
        if item is None:
            raise NotFoundError("Catalog item not found")
        try:
            return await self.repo.add_barcode(
                tenant_id=tenant_id,
                catalog_id=catalog_id,
                code=code,
                code_type=code_type,
            )
        except Exception as exc:
            # Unique-violation surfaces as IntegrityError; map to a domain error.
            msg = str(exc).lower()
            if "unique" in msg or "uq_barcode" in msg or "duplicate key" in msg:
                raise ConflictError(
                    "Barcode already exists in this tenant",
                    details={"code": code},
                ) from exc
            raise

    async def find_item_by_barcode(self, code: str, *, tenant_id: UUID) -> TenantCatalog:
        item = await self.repo.find_item_by_barcode(code, tenant_id=tenant_id)
        if item is None:
            raise NotFoundError("No catalog item for this barcode")
        return item

    async def delete_barcode(self, *, catalog_id: UUID, barcode_id: UUID) -> None:
        # Validate the item is visible (RLS aside, soft-deleted items shouldn't
        # have their barcodes changed).
        await self.get_item(catalog_id)
        rows = await self.repo.delete_barcode(barcode_id, catalog_id=catalog_id)
        if rows == 0:
            raise NotFoundError("Barcode not found")

    # -------------------------------------------------------------------------
    # Import
    # -------------------------------------------------------------------------

    async def create_import_job(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        source_filename: str,
        source_path: str,
    ) -> CatalogImportJob:
        return await self.repo.create_job(
            tenant_id=tenant_id,
            user_id=user_id,
            source_filename=source_filename,
            source_path=source_path,
            status="pending",
        )

    async def preview_import(self, *, job_id: UUID, raw: bytes) -> CatalogImportJob:
        job = await self._get_job_or_404(job_id)
        if job.status not in ("pending", "validating"):
            raise BusinessRuleError(
                "Cannot preview an import that is already running or finished",
                details={"status": job.status},
            )
        try:
            rows, errors = await asyncio.to_thread(parse_import, raw, job.source_filename)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        await self.repo.update_job(
            job,
            status="validating",
            total_rows=len(rows) + len(errors),
            valid_rows=len(rows),
            error_rows=len(errors),
            preview_data=[_to_jsonable(r) for r in rows[:PREVIEW_ROW_LIMIT]],
            errors=errors[:PREVIEW_ROW_LIMIT],
        )
        return job

    async def get_job(self, job_id: UUID) -> CatalogImportJob:
        return await self._get_job_or_404(job_id)

    async def confirm_import(
        self,
        *,
        job_id: UUID,
        duplicate_strategy: str,
    ) -> CatalogImportJob:
        job = await self.repo.get_job_for_update(job_id)
        if job is None:
            raise NotFoundError("Import job not found")
        if job.status == "importing":
            if job.duplicate_strategy != duplicate_strategy:
                raise BusinessRuleError(
                    "Import is already running with another duplicate strategy",
                    details={"status": job.status},
                )
            return job
        if job.status not in ("pending", "validating"):
            raise BusinessRuleError(
                "Cannot confirm an import that is already running or finished",
                details={"status": job.status},
            )
        await self.repo.lock_import_slot(job.tenant_id)
        active = await self.repo.get_active_import(job.tenant_id)
        if active is not None and active.id != job.id:
            raise BusinessRuleError(
                "Another catalog import is already running for this pharmacy",
                details={"reason": "catalog_import_in_progress"},
            )
        await self.repo.update_job(
            job,
            status="importing",
            duplicate_strategy=duplicate_strategy,
            started_at=utc_now(),
        )

        return job

    async def process_import(
        self,
        *,
        job_id: UUID,
        raw: bytes,
    ) -> CatalogImportJob:
        """Run synchronously inside the Celery worker (or a test). Reads the
        already-uploaded bytes, applies the duplicate strategy, writes one
        tenant_catalog row per valid parsed row, tags every row with
        import_job_id so rollback can find it later."""
        job = await self.repo.get_job_for_update(job_id)
        if job is None:
            raise NotFoundError("Import job not found")
        if job.status in ("success", "failed", "rolled_back"):
            return job
        if job.status != "importing":
            raise BusinessRuleError(
                "Import has not been confirmed",
                details={"status": job.status},
            )
        try:
            rows, errors = parse_import(raw, job.source_filename)
        except ValueError as exc:
            await self.repo.update_job(
                job,
                status="failed",
                errors=[{"row": 0, "messages": [str(exc)]}],
                finished_at=utc_now(),
            )
            return job

        created = 0
        updated = 0
        skipped = 0
        per_row_errors: list[dict[str, Any]] = list(errors)
        strategy = job.duplicate_strategy

        for row in rows:
            try:
                async with self.repo.row_savepoint():
                    outcome = await self._apply_import_row(job, row, strategy=strategy)

                if outcome == "created":
                    created += 1
                elif outcome == "updated":
                    updated += 1
                else:
                    skipped += 1
            except ConflictError as exc:
                per_row_errors.append(
                    {"row": row.get("brand_name", "?"), "messages": [exc.message]}
                )
            except Exception:
                logger.exception("catalog_import_row_failed", job_id=str(job.id))
                per_row_errors.append(
                    {
                        "row": row.get("brand_name", "?"),
                        "messages": ["Не удалось обработать строку"],
                    }
                )

        now = utc_now()
        any_progress = bool(created or updated or skipped)
        final_status = "failed" if not any_progress and per_row_errors else "success"
        return await self.repo.update_job(
            job,
            status=final_status,
            total_rows=len(rows) + len(errors),
            valid_rows=created + updated + skipped,
            error_rows=len(per_row_errors),
            errors=per_row_errors[:PREVIEW_ROW_LIMIT] if per_row_errors else None,
            finished_at=now,
            expires_at_for_rollback=now + ROLLBACK_WINDOW,
        )

    async def _apply_import_row(
        self,
        job: CatalogImportJob,
        source_row: dict[str, Any],
        *,
        strategy: str,
    ) -> Literal["created", "updated", "skipped"]:
        row = dict(source_row)
        barcode_code = row.pop("barcode", None)
        duplicate = await self.repo.find_duplicate(
            tenant_id=job.tenant_id,
            brand_name=row["brand_name"],
            manufacturer=row.get("manufacturer"),
            dosage=row.get("dosage"),
            pack_size=row.get("pack_size"),
        )
        outcome: Literal["created", "updated", "skipped"] = "skipped"
        target_id: UUID | None = None
        if duplicate is not None:
            target_id = duplicate.id
            if strategy == "update":
                merged = {key: value for key, value in row.items() if value is not None}
                await self.repo.update_item(duplicate, **merged)
                outcome = "updated"
            elif strategy == "create_copy":
                item = await self.repo.create_item(
                    tenant_id=job.tenant_id,
                    import_job_id=job.id,
                    created_by=job.user_id,
                    **row,
                )
                target_id = item.id
                outcome = "created"
        else:
            item = await self.repo.create_item(
                tenant_id=job.tenant_id,
                import_job_id=job.id,
                created_by=job.user_id,
                **row,
            )
            target_id = item.id
            outcome = "created"

        if barcode_code and target_id is not None:
            await self.add_barcode(
                tenant_id=job.tenant_id,
                catalog_id=target_id,
                code=barcode_code,
                code_type="ean13",
            )
        return outcome

    async def rollback_import(self, job_id: UUID) -> CatalogImportJob:
        job = await self.repo.get_job_for_update(job_id)
        if job is None:
            raise NotFoundError("Import job not found")
        if job.status not in ("success", "failed"):
            raise BusinessRuleError(
                "Import has not finished yet",
                details={"status": job.status},
            )
        if job.expires_at_for_rollback is None or utc_now() > job.expires_at_for_rollback:
            raise BusinessRuleError(
                "Rollback window has expired (24 hours after import)",
            )
        if job.rolled_back_at is not None:
            raise BusinessRuleError("Import has already been rolled back")
        if await self.repo.import_job_has_operational_dependencies(
            tenant_id=job.tenant_id,
            import_job_id=job.id,
        ):
            raise BusinessRuleError(
                "Imported products already have receipts or stock movements "
                "and cannot be rolled back",
                details={"reason": "catalog_import_has_operational_dependencies"},
            )

        now = utc_now()
        await self.repo.soft_delete_by_import_job(job.id, when=now)
        return await self.repo.update_job(job, status="rolled_back", rolled_back_at=now)

    async def _get_job_or_404(self, job_id: UUID) -> CatalogImportJob:
        job = await self.repo.get_job(job_id)
        if job is None:
            raise NotFoundError("Import job not found")
        return job


def _to_jsonable(row: dict[str, Any]) -> dict[str, Any]:
    """Decimal isn't JSON-serializable; convert to str for preview storage."""
    out: dict[str, Any] = {}
    for key, value in row.items():
        if hasattr(value, "as_tuple"):  # Decimal
            out[key] = str(value)
        else:
            out[key] = value
    return out


# Expose the constant generator the router uses to choose its filename.
def new_import_object_name(tenant_id: UUID) -> str:
    return f"{tenant_id}/imports/{uuid4()}.csv"


def new_catalog_image_object_name(
    tenant_id: UUID,
    item_id: UUID,
    version: UUID,
    variant: Literal["display", "thumbnail"],
) -> str:
    return f"{tenant_id}/catalog/{item_id}/images/{version}/{variant}.webp"
