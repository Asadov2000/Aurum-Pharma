"""FastAPI endpoints for the catalog domain.

Routes:
- /api/v1/catalog                      list / create
- /api/v1/catalog/{id}                 read / update / delete
- /api/v1/catalog/by-barcode/{code}    scan-to-product lookup
- /api/v1/catalog/{id}/barcodes        add / remove
- /api/v1/catalog/import/upload        multipart upload to MinIO
- /api/v1/catalog/import/{id}/preview  dry-run, sets preview_data
- /api/v1/catalog/import/{id}/confirm  kick the Celery import task
- /api/v1/catalog/import/{id}          status
- /api/v1/catalog/import/{id}/rollback soft-delete every row this job created
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import CurrentUser, get_db, require_permission
from app.core.errors import BusinessRuleError, ValidationError
from app.core.storage import put_object
from app.domains.catalog.import_parser import XLS_UNSUPPORTED_MESSAGE
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.schemas import (
    BarcodeCreate,
    BarcodeRead,
    CatalogItemCreate,
    CatalogItemRead,
    CatalogItemUpdate,
    CatalogItemWithBarcodes,
    CatalogLifecycle,
    CatalogList,
    ImportConfirmRequest,
    ImportJobRead,
)
from app.domains.catalog.service import CatalogService, new_import_object_name

router = APIRouter(prefix="/api/v1/catalog", tags=["catalog"])
UPLOAD_CHUNK_BYTES = 1024 * 1024


async def _read_import_file(file: UploadFile, *, max_bytes: int) -> bytes:
    data = bytearray()
    while chunk := await file.read(UPLOAD_CHUNK_BYTES):
        data.extend(chunk)
        if len(data) > max_bytes:
            max_mb = max_bytes // (1024 * 1024)
            raise ValidationError(f"Файл больше {max_mb} МБ — уменьшите его и попробуйте снова")
    return bytes(data)


async def _service(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> CatalogService:
    return CatalogService(CatalogRepository(db))


def _current_tenant_or_400(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise BusinessRuleError(
            "Request is not scoped to a tenant",
            details={"hint": "Login as a tenant user or pass X-Tenant-Id (phase 2)."},
        )
    return user.tenant_id


# -----------------------------------------------------------------------------
# CRUD + search
# -----------------------------------------------------------------------------


@router.get("", response_model=CatalogList)
async def list_catalog(
    _user: Annotated[CurrentUser, Depends(require_permission("catalog.view"))],
    service: Annotated[CatalogService, Depends(_service)],
    q: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    dispensing_type: Annotated[str | None, Query()] = None,
    manufacturer: Annotated[str | None, Query(max_length=500)] = None,
    storage_type: Annotated[str | None, Query()] = None,
    lifecycle: Annotated[CatalogLifecycle, Query()] = "active",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    branch_id: Annotated[UUID | None, Query()] = None,
) -> CatalogList:
    items, total, stock = await service.search(
        q=q,
        category=category,
        dispensing_type=dispensing_type,
        manufacturer=manufacturer,
        storage_type=storage_type,
        lifecycle=lifecycle,
        page=page,
        page_size=page_size,
        branch_id=branch_id,
    )
    return CatalogList(
        items=[
            CatalogItemRead.model_validate(i).model_copy(
                update={"stock_available": stock.get(i.id)}
            )
            for i in items
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=CatalogItemRead, status_code=status.HTTP_201_CREATED)
async def create_catalog_item(
    payload: CatalogItemCreate,
    user: Annotated[CurrentUser, Depends(require_permission("catalog.create"))],
    service: Annotated[CatalogService, Depends(_service)],
) -> CatalogItemRead:
    item = await service.create_item(
        tenant_id=_current_tenant_or_400(user),
        fields=payload.model_dump(exclude_none=True),
        created_by=user.user_id,
    )
    return CatalogItemRead.model_validate(item)


@router.get("/by-barcode/{code}", response_model=CatalogItemRead)
async def get_by_barcode(
    code: str,
    _user: Annotated[CurrentUser, Depends(require_permission("catalog.view"))],
    service: Annotated[CatalogService, Depends(_service)],
) -> CatalogItemRead:
    item = await service.find_item_by_barcode(code)
    return CatalogItemRead.model_validate(item)


@router.get("/{item_id}", response_model=CatalogItemWithBarcodes)
async def get_catalog_item(
    item_id: UUID,
    _user: Annotated[CurrentUser, Depends(require_permission("catalog.view"))],
    service: Annotated[CatalogService, Depends(_service)],
) -> CatalogItemWithBarcodes:
    item, barcodes = await service.get_item_with_barcodes(item_id, include_deleted=True)
    return CatalogItemWithBarcodes(
        **CatalogItemRead.model_validate(item).model_dump(),
        barcodes=[BarcodeRead.model_validate(b) for b in barcodes],
    )


@router.patch("/{item_id}", response_model=CatalogItemRead)
async def update_catalog_item(
    item_id: UUID,
    payload: CatalogItemUpdate,
    user: Annotated[CurrentUser, Depends(require_permission("catalog.update"))],
    service: Annotated[CatalogService, Depends(_service)],
) -> CatalogItemRead:
    item = await service.update_item(
        item_id,
        fields=payload.model_dump(exclude_unset=True),
        updated_by=user.user_id,
    )
    return CatalogItemRead.model_validate(item)


@router.delete("/{item_id}")
async def soft_delete_catalog_item(
    item_id: UUID,
    _user: Annotated[CurrentUser, Depends(require_permission("catalog.delete"))],
    service: Annotated[CatalogService, Depends(_service)],
) -> dict[str, str]:
    await service.soft_delete_item(item_id)
    return {"status": "deleted"}


@router.post("/{item_id}/restore", response_model=CatalogItemRead)
async def restore_catalog_item(
    item_id: UUID,
    _user: Annotated[CurrentUser, Depends(require_permission("catalog.delete"))],
    service: Annotated[CatalogService, Depends(_service)],
) -> CatalogItemRead:
    item = await service.restore_item(item_id)
    return CatalogItemRead.model_validate(item)


# -----------------------------------------------------------------------------
# Barcodes
# -----------------------------------------------------------------------------


@router.post(
    "/{item_id}/barcodes",
    response_model=BarcodeRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_barcode(
    item_id: UUID,
    payload: BarcodeCreate,
    user: Annotated[CurrentUser, Depends(require_permission("catalog.update"))],
    service: Annotated[CatalogService, Depends(_service)],
) -> BarcodeRead:
    bc = await service.add_barcode(
        tenant_id=_current_tenant_or_400(user),
        catalog_id=item_id,
        code=payload.code,
        code_type=payload.code_type,
    )
    return BarcodeRead.model_validate(bc)


@router.delete("/{item_id}/barcodes/{barcode_id}")
async def delete_barcode(
    item_id: UUID,
    barcode_id: UUID,
    _user: Annotated[CurrentUser, Depends(require_permission("catalog.update"))],
    service: Annotated[CatalogService, Depends(_service)],
) -> dict[str, str]:
    await service.delete_barcode(catalog_id=item_id, barcode_id=barcode_id)
    return {"status": "deleted"}


# -----------------------------------------------------------------------------
# Import
# -----------------------------------------------------------------------------


@router.post(
    "/import/upload",
    response_model=ImportJobRead,
    status_code=status.HTTP_201_CREATED,
)
async def import_upload(
    user: Annotated[CurrentUser, Depends(require_permission("catalog.create"))],
    service: Annotated[CatalogService, Depends(_service)],
    file: UploadFile = File(...),  # noqa: B008 — FastAPI File() pattern
) -> ImportJobRead:
    tenant_id = _current_tenant_or_400(user)

    name = (file.filename or "").lower()
    if name.endswith(".xls"):  # legacy binary format — openpyxl can't read it
        raise ValidationError(XLS_UNSUPPORTED_MESSAGE)

    max_mb = get_settings().MAX_IMPORT_FILE_MB
    data = await _read_import_file(file, max_bytes=max_mb * 1024 * 1024)

    object_name = new_import_object_name(tenant_id)
    source_path = put_object(
        object_name=object_name,
        data=data,
        content_type=file.content_type or "text/csv",
    )
    job = await service.create_import_job(
        tenant_id=tenant_id,
        user_id=user.user_id,
        source_filename=file.filename or "upload.csv",
        source_path=source_path,
    )
    return ImportJobRead.model_validate(job)


@router.post("/import/{job_id}/preview", response_model=ImportJobRead)
async def import_preview(
    job_id: UUID,
    _user: Annotated[CurrentUser, Depends(require_permission("catalog.create"))],
    service: Annotated[CatalogService, Depends(_service)],
) -> ImportJobRead:
    job = await service.get_job(job_id)
    if not job.source_path:
        raise BusinessRuleError("Job has no uploaded file")
    from app.core.storage import get_object

    raw = get_object(job.source_path)
    job = await service.preview_import(job_id=job_id, raw=raw)
    return ImportJobRead.model_validate(job)


@router.post("/import/{job_id}/confirm", response_model=ImportJobRead)
async def import_confirm(
    job_id: UUID,
    payload: ImportConfirmRequest,
    _create_user: Annotated[CurrentUser, Depends(require_permission("catalog.create"))],
    _update_user: Annotated[CurrentUser, Depends(require_permission("catalog.update"))],
    service: Annotated[CatalogService, Depends(_service)],
) -> ImportJobRead:
    job = await service.confirm_import(job_id=job_id, duplicate_strategy=payload.duplicate_strategy)
    return ImportJobRead.model_validate(job)


@router.get("/import/{job_id}", response_model=ImportJobRead)
async def import_status(
    job_id: UUID,
    _user: Annotated[CurrentUser, Depends(require_permission("catalog.create"))],
    service: Annotated[CatalogService, Depends(_service)],
) -> ImportJobRead:
    job = await service.get_job(job_id)
    return ImportJobRead.model_validate(job)


@router.post("/import/{job_id}/rollback", response_model=ImportJobRead)
async def import_rollback(
    job_id: UUID,
    _user: Annotated[CurrentUser, Depends(require_permission("catalog.delete"))],
    service: Annotated[CatalogService, Depends(_service)],
) -> ImportJobRead:
    job = await service.rollback_import(job_id)
    return ImportJobRead.model_validate(job)
