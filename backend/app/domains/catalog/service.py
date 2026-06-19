"""Business logic for the catalog domain.

CRUD on tenant_catalog + barcode operations, plus the import pipeline:
upload → preview (dry-run, first 100 parsed rows) → confirm (Celery) →
status → rollback (24 h after finished_at).

`process_import` is called by the Celery task `import_catalog_job`; it is
also reachable directly from tests for deterministic exercise of the
duplicate-handling branches.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import structlog

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.core.time import utc_now
from app.domains.catalog.import_parser import parse_import
from app.domains.catalog.models import (
    Barcode,
    CatalogImportJob,
    TenantCatalog,
)
from app.domains.catalog.repository import CatalogRepository

logger = structlog.get_logger("catalog.service")

PREVIEW_ROW_LIMIT = 100
ROLLBACK_WINDOW = timedelta(hours=24)


class CatalogService:
    def __init__(self, repo: CatalogRepository) -> None:
        self.repo = repo

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

    async def get_item(self, item_id: UUID) -> TenantCatalog:
        item = await self.repo.get_item(item_id)
        if item is None:
            raise NotFoundError("Catalog item not found")
        return item

    async def get_item_with_barcodes(self, item_id: UUID) -> tuple[TenantCatalog, list[Barcode]]:
        item = await self.get_item(item_id)
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

    async def search(
        self,
        *,
        q: str | None,
        category: str | None,
        dispensing_type: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[TenantCatalog], int]:
        return await self.repo.search(
            q=q,
            category=category,
            dispensing_type=dispensing_type,
            page=page,
            page_size=page_size,
        )

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

    async def find_item_by_barcode(self, code: str) -> TenantCatalog:
        item = await self.repo.find_item_by_barcode(code)
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
        rows, errors = parse_import(raw, job.source_filename)
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
        job = await self._get_job_or_404(job_id)
        if job.status not in ("pending", "validating"):
            raise BusinessRuleError(
                "Cannot confirm an import that is already running or finished",
                details={"status": job.status},
            )
        await self.repo.update_job(
            job,
            status="importing",
            duplicate_strategy=duplicate_strategy,
            started_at=utc_now(),
        )

        # Kick the Celery task (lazy import — circular-aware).
        from app.tasks.catalog import import_catalog_job

        import_catalog_job.delay(str(job_id))
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
        job = await self._get_job_or_404(job_id)
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
            barcode_code = row.pop("barcode", None)
            try:
                duplicate = await self.repo.find_duplicate(
                    tenant_id=job.tenant_id,
                    brand_name=row["brand_name"],
                    manufacturer=row.get("manufacturer"),
                    dosage=row.get("dosage"),
                    pack_size=row.get("pack_size"),
                )
                target_id: UUID | None = None
                if duplicate is not None:
                    if strategy == "skip":
                        skipped += 1
                        target_id = duplicate.id
                    elif strategy == "update":
                        merged = {k: v for k, v in row.items() if v is not None}
                        await self.repo.update_item(duplicate, **merged)
                        updated += 1
                        target_id = duplicate.id
                    elif strategy == "create_copy":
                        item = await self.repo.create_item(
                            tenant_id=job.tenant_id,
                            import_job_id=job.id,
                            created_by=job.user_id,
                            **row,
                        )
                        created += 1
                        target_id = item.id
                else:
                    item = await self.repo.create_item(
                        tenant_id=job.tenant_id,
                        import_job_id=job.id,
                        created_by=job.user_id,
                        **row,
                    )
                    created += 1
                    target_id = item.id

                if barcode_code and target_id is not None:
                    try:
                        await self.repo.add_barcode(
                            tenant_id=job.tenant_id,
                            catalog_id=target_id,
                            code=barcode_code,
                            code_type="ean13",
                        )
                    except Exception as exc:
                        per_row_errors.append({"row": "barcode", "messages": [str(exc)]})
            except Exception as exc:
                per_row_errors.append({"row": row.get("brand_name", "?"), "messages": [str(exc)]})

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

    async def rollback_import(self, job_id: UUID) -> CatalogImportJob:
        job = await self._get_job_or_404(job_id)
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
