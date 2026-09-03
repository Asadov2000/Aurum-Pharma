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

from typing import Annotated, Literal
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import (
    CurrentUser,
    get_db,
    require_any_branch_permission,
    require_permission,
)
from app.core.errors import (
    BusinessRuleError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.core.storage import default_object_path, get_object, put_object, remove_object
from app.domains.catalog.image_processing import process_catalog_image
from app.domains.catalog.import_parser import XLS_UNSUPPORTED_MESSAGE
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.schemas import (
    BarcodeCreate,
    BarcodeRead,
    CatalogBarcodeState,
    CatalogImageState,
    CatalogItemCreate,
    CatalogItemRead,
    CatalogItemUpdate,
    CatalogItemWithBarcodes,
    CatalogLifecycle,
    CatalogList,
    CatalogPickerList,
    CatalogSummary,
    ImportConfirmRequest,
    ImportJobRead,
)
from app.domains.catalog.service import (
    CatalogService,
    new_catalog_image_object_name,
    new_import_object_name,
)

router = APIRouter(prefix="/api/v1/catalog", tags=["catalog"])
UPLOAD_CHUNK_BYTES = 1024 * 1024
logger = structlog.get_logger("catalog.router")


async def _read_import_file(file: UploadFile, *, max_bytes: int) -> bytes:
    data = bytearray()
    while chunk := await file.read(UPLOAD_CHUNK_BYTES):
        data.extend(chunk)
        if len(data) > max_bytes:
            max_mb = max_bytes // (1024 * 1024)
            raise ValidationError(f"Файл больше {max_mb} МБ — уменьшите его и попробуйте снова")
    return bytes(data)


async def _read_catalog_image(file: UploadFile, *, max_bytes: int) -> bytes:
    data = bytearray()
    while chunk := await file.read(UPLOAD_CHUNK_BYTES):
        data.extend(chunk)
        if len(data) > max_bytes:
            max_mb = max_bytes // (1024 * 1024)
            raise ValidationError(
                f"Изображение больше {max_mb} МБ — выберите файл меньшего размера"
            )
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


def _authorize_catalog_search(user: CurrentUser, branch_id: UUID | None) -> None:
    if user.has_tenant_scope("catalog.view"):
        return
    if branch_id is None:
        raise BusinessRuleError("branch_id is required for branch-scoped POS catalog search")
    if not user.can_access_branch("pos.sell", branch_id):
        raise PermissionDeniedError("Branch access denied")


def _remove_catalog_image_version(tenant_id: UUID, item_id: UUID, version: UUID) -> None:
    variants: tuple[Literal["display", "thumbnail"], ...] = ("display", "thumbnail")
    for variant in variants:
        object_name = new_catalog_image_object_name(tenant_id, item_id, version, variant)
        try:
            remove_object(default_object_path(object_name))
        except Exception as exc:
            logger.warning(
                "catalog_image_cleanup_failed",
                item_id=str(item_id),
                version=str(version),
                variant=variant,
                error_type=type(exc).__name__,
            )


# -----------------------------------------------------------------------------
# CRUD + search
# -----------------------------------------------------------------------------


@router.get("/picker", response_model=CatalogPickerList)
async def search_catalog_picker(
    user: Annotated[
        CurrentUser,
        Depends(require_any_branch_permission("catalog.view", "pos.sell", policy="filter")),
    ],
    service: Annotated[CatalogService, Depends(_service)],
    q: Annotated[str, Query(min_length=2, max_length=200)],
    branch_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
) -> CatalogPickerList:
    _authorize_catalog_search(user, branch_id)
    items, stock = await service.search_picker(
        q=q,
        branch_id=branch_id,
        limit=limit,
        tenant_id=_current_tenant_or_400(user),
    )
    return CatalogPickerList(
        items=[
            CatalogItemRead.model_validate(item).model_copy(
                update={"stock_available": stock.get(item.id)}
            )
            for item in items
        ]
    )


@router.get("", response_model=CatalogList)
async def list_catalog(
    user: Annotated[
        CurrentUser,
        Depends(
            require_any_branch_permission(
                "catalog.view",
                "pos.sell",
                policy="filter",
            )
        ),
    ],
    service: Annotated[CatalogService, Depends(_service)],
    q: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    dispensing_type: Annotated[str | None, Query()] = None,
    manufacturer: Annotated[str | None, Query(max_length=500)] = None,
    storage_type: Annotated[str | None, Query()] = None,
    lifecycle: Annotated[CatalogLifecycle, Query()] = "active",
    image_state: Annotated[CatalogImageState, Query()] = "any",
    barcode_state: Annotated[CatalogBarcodeState, Query()] = "any",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    branch_id: Annotated[UUID | None, Query()] = None,
) -> CatalogList:
    _authorize_catalog_search(user, branch_id)
    items, total, stock = await service.search(
        q=q,
        category=category,
        dispensing_type=dispensing_type,
        manufacturer=manufacturer,
        storage_type=storage_type,
        lifecycle=lifecycle,
        image_state=image_state,
        barcode_state=barcode_state,
        page=page,
        page_size=page_size,
        branch_id=branch_id,
        tenant_id=_current_tenant_or_400(user),
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


@router.get("/summary", response_model=CatalogSummary)
async def get_catalog_summary(
    _user: Annotated[CurrentUser, Depends(require_permission("catalog.view"))],
    service: Annotated[CatalogService, Depends(_service)],
) -> CatalogSummary:
    return CatalogSummary.model_validate(await service.summary())


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


@router.get("/{item_id}/image/{version}/{variant}")
async def get_catalog_image(
    item_id: UUID,
    version: UUID,
    variant: Literal["display", "thumbnail"],
    user: Annotated[CurrentUser, Depends(require_permission("catalog.view"))],
    service: Annotated[CatalogService, Depends(_service)],
) -> Response:
    item = await service.get_item(item_id, include_deleted=True)
    if item.tenant_id != _current_tenant_or_400(user) or item.image_version != version:
        raise NotFoundError("Catalog image not found")
    object_name = new_catalog_image_object_name(item.tenant_id, item.id, version, variant)
    raw = await run_in_threadpool(get_object, default_object_path(object_name))
    return Response(
        content=raw,
        media_type="image/webp",
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "Content-Disposition": "inline",
            "ETag": f'"{version}-{variant}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.put("/{item_id}/image", response_model=CatalogItemRead)
async def upload_catalog_image(
    item_id: UUID,
    background_tasks: BackgroundTasks,
    user: Annotated[CurrentUser, Depends(require_permission("catalog.update"))],
    service: Annotated[CatalogService, Depends(_service)],
    file: UploadFile = File(...),  # noqa: B008 - FastAPI File() pattern
) -> CatalogItemRead:
    tenant_id = _current_tenant_or_400(user)
    item = await service.get_item(item_id)
    if item.tenant_id != tenant_id:
        raise NotFoundError("Catalog item not found")

    max_bytes = get_settings().MAX_CATALOG_IMAGE_MB * 1024 * 1024
    raw = await _read_catalog_image(file, max_bytes=max_bytes)
    image = await run_in_threadpool(process_catalog_image, raw, file.content_type)
    version = uuid4()
    uploaded_paths: list[str] = []
    try:
        variants: tuple[tuple[Literal["display", "thumbnail"], bytes], ...] = (
            ("display", image.display),
            ("thumbnail", image.thumbnail),
        )
        for variant, data in variants:
            object_name = new_catalog_image_object_name(tenant_id, item.id, version, variant)
            uploaded_paths.append(
                await run_in_threadpool(
                    put_object,
                    object_name=object_name,
                    data=data,
                    content_type="image/webp",
                )
            )
        updated, old_version = await service.set_image_metadata(
            item.id,
            version=version,
            image=image,
            updated_by=user.user_id,
        )
    except Exception:
        for object_path in uploaded_paths:
            try:
                await run_in_threadpool(remove_object, object_path)
            except Exception as cleanup_exc:
                logger.warning(
                    "catalog_image_upload_rollback_failed",
                    item_id=str(item.id),
                    error_type=type(cleanup_exc).__name__,
                )
        raise
    if old_version and old_version != version:
        background_tasks.add_task(_remove_catalog_image_version, tenant_id, item.id, old_version)
    return CatalogItemRead.model_validate(updated)


@router.delete("/{item_id}/image", response_model=CatalogItemRead)
async def delete_catalog_image(
    item_id: UUID,
    background_tasks: BackgroundTasks,
    user: Annotated[CurrentUser, Depends(require_permission("catalog.update"))],
    service: Annotated[CatalogService, Depends(_service)],
) -> CatalogItemRead:
    tenant_id = _current_tenant_or_400(user)
    item = await service.get_item(item_id)
    if item.tenant_id != tenant_id:
        raise NotFoundError("Catalog item not found")
    updated, old_version = await service.clear_image_metadata(item_id, updated_by=user.user_id)
    if old_version:
        background_tasks.add_task(_remove_catalog_image_version, tenant_id, item_id, old_version)
    return CatalogItemRead.model_validate(updated)


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
