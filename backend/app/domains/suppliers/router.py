"""FastAPI endpoints for suppliers and supplier returns."""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_any_permission, require_permission
from app.core.errors import BusinessRuleError
from app.domains.suppliers.repository import SupplierReturnRow, SuppliersRepository
from app.domains.suppliers.schemas import (
    SupplierCreate,
    SupplierOptionList,
    SupplierOptionRead,
    SupplierOptionSearchRequest,
    SupplierRead,
    SupplierReturnCandidate,
    SupplierReturnCandidateList,
    SupplierReturnCandidateSearchRequest,
    SupplierReturnCreate,
    SupplierReturnCreated,
    SupplierReturnDetails,
    SupplierReturnList,
    SupplierReturnRead,
    SupplierReturnSearchRequest,
    SupplierReturnSummary,
    SupplierSearchRequest,
    SupplierSearchResponse,
    SupplierSearchSummary,
    SupplierUpdate,
)
from app.domains.suppliers.service import SuppliersService

router = APIRouter(prefix="/api/v1/suppliers", tags=["suppliers"])


async def _service(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> SuppliersService:
    return SuppliersService(SuppliersRepository(db))


def _current_tenant_or_400(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise BusinessRuleError("Request is not scoped to a tenant")
    return user.tenant_id


def _set_search_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"


def _return_details(row: SupplierReturnRow, *, report_timezone: str) -> SupplierReturnDetails:
    return SupplierReturnDetails(
        **SupplierReturnRead.model_validate(row.supplier_return).model_dump(),
        supplier_name=row.supplier_name,
        branch_id=row.branch_id,
        branch_name=row.branch_name,
        batch_number=row.batch_number,
        catalog_name=row.catalog_name,
        catalog_form=row.catalog_form,
        catalog_dosage=row.catalog_dosage,
        catalog_pack_size=row.catalog_pack_size,
        source_document_number=row.source_document_number,
        report_timezone=report_timezone,
    )


@router.post(
    "/returns/candidates/search",
    response_model=SupplierReturnCandidateList,
)
async def search_supplier_return_candidates(
    payload: SupplierReturnCandidateSearchRequest,
    response: Response,
    user: Annotated[CurrentUser, Depends(require_permission("incoming.return"))],
    service: Annotated[SuppliersService, Depends(_service)],
) -> SupplierReturnCandidateList:
    _set_search_no_store(response)
    rows, total = await service.search_return_candidates(
        tenant_id=_current_tenant_or_400(user),
        supplier_id=payload.supplier_id,
        branch_id=payload.branch_id,
        branch_ids=user.branch_scope_for("incoming.return"),
        q=payload.q,
        page=payload.page,
        page_size=payload.page_size,
    )
    return SupplierReturnCandidateList(
        items=[
            SupplierReturnCandidate(
                batch_id=row.batch.id,
                source_document_id=row.source_document_id,
                document_number=row.document_number,
                document_date=row.document_date,
                branch_id=row.batch.branch_id,
                branch_name=row.branch_name,
                catalog_name=row.catalog_name,
                catalog_form=row.catalog_form,
                catalog_dosage=row.catalog_dosage,
                catalog_pack_size=row.catalog_pack_size,
                batch_number=row.batch.batch_number,
                expires_at=row.batch.expires_at,
                qty_remaining=row.batch.qty_remaining,
                purchase_price=row.batch.purchase_price,
                currency=row.batch.currency,
            )
            for row in rows
        ],
        total=total,
        page=payload.page,
        page_size=payload.page_size,
    )


@router.post("/returns/search", response_model=SupplierReturnList)
async def search_supplier_returns(
    payload: SupplierReturnSearchRequest,
    response: Response,
    user: Annotated[CurrentUser, Depends(require_permission("incoming.view"))],
    service: Annotated[SuppliersService, Depends(_service)],
) -> SupplierReturnList:
    _set_search_no_store(response)
    rows, summary, report_timezone = await service.search_returns(
        tenant_id=_current_tenant_or_400(user),
        supplier_id=payload.supplier_id,
        branch_id=payload.branch_id,
        branch_ids=user.branch_scope_for("incoming.view"),
        reason=payload.reason,
        date_from=payload.date_from,
        date_to=payload.date_to,
        page=payload.page,
        page_size=payload.page_size,
    )
    return SupplierReturnList(
        items=[_return_details(row, report_timezone=report_timezone) for row in rows],
        total=summary.total,
        page=payload.page,
        page_size=payload.page_size,
        summary=SupplierReturnSummary(
            total_qty=summary.total_qty,
            total_amount=summary.total_amount,
        ),
    )


@router.post(
    "/returns",
    response_model=SupplierReturnCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_supplier_return(
    payload: SupplierReturnCreate,
    user: Annotated[CurrentUser, Depends(require_permission("incoming.return"))],
    service: Annotated[SuppliersService, Depends(_service)],
) -> SupplierReturnCreated:
    supplier_return = await service.create_return(
        operation_id=payload.operation_id,
        tenant_id=_current_tenant_or_400(user),
        supplier_id=payload.supplier_id,
        batch_id=payload.batch_id,
        qty=payload.qty,
        reason=payload.reason,
        comment=payload.comment,
        source_document_id=payload.source_document_id,
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("incoming.return"),
    )
    return SupplierReturnCreated(
        **SupplierReturnRead.model_validate(supplier_return).model_dump(),
        warning=None,
    )


@router.get("/returns", response_model=list[SupplierReturnRead])
async def list_supplier_returns(
    user: Annotated[CurrentUser, Depends(require_permission("incoming.view"))],
    service: Annotated[SuppliersService, Depends(_service)],
    supplier_id: Annotated[UUID | None, Query()] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> list[SupplierReturnRead]:
    rows, _summary, _timezone = await service.search_returns(
        tenant_id=_current_tenant_or_400(user),
        supplier_id=supplier_id,
        branch_id=None,
        branch_ids=user.branch_scope_for("incoming.view"),
        reason=None,
        date_from=date_from,
        date_to=date_to,
        page=1,
        page_size=100,
    )
    return [SupplierReturnRead.model_validate(row.supplier_return) for row in rows]


@router.post("/options/search", response_model=SupplierOptionList)
async def search_supplier_options(
    payload: SupplierOptionSearchRequest,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(
            require_any_permission(
                "suppliers.view",
                "incoming.create",
                "incoming.return",
            )
        ),
    ],
    service: Annotated[SuppliersService, Depends(_service)],
) -> SupplierOptionList:
    _set_search_no_store(response)
    items = await service.search_supplier_options(
        tenant_id=_current_tenant_or_400(user),
        q=payload.q,
        include_inactive=payload.include_inactive,
        selected_id=payload.selected_id,
        limit=payload.limit,
    )
    return SupplierOptionList(
        items=[
            SupplierOptionRead(id=item.id, name=item.name, is_active=item.is_active)
            for item in items
        ]
    )


@router.get("", response_model=list[SupplierRead])
async def list_suppliers(
    user: Annotated[CurrentUser, Depends(require_permission("suppliers.view"))],
    service: Annotated[SuppliersService, Depends(_service)],
    include_inactive: Annotated[bool, Query()] = False,
) -> list[SupplierRead]:
    items = await service.list_suppliers(
        tenant_id=_current_tenant_or_400(user),
        include_inactive=include_inactive,
    )
    return [SupplierRead.model_validate(item) for item in items]


@router.post("/search", response_model=SupplierSearchResponse)
async def search_suppliers(
    payload: SupplierSearchRequest,
    response: Response,
    user: Annotated[CurrentUser, Depends(require_permission("suppliers.view"))],
    service: Annotated[SuppliersService, Depends(_service)],
) -> SupplierSearchResponse:
    _set_search_no_store(response)
    items, total, summary = await service.search_suppliers(
        tenant_id=_current_tenant_or_400(user),
        q=payload.q,
        is_active=payload.is_active,
        page=payload.page,
        page_size=payload.page_size,
    )
    return SupplierSearchResponse(
        items=[SupplierRead.model_validate(item) for item in items],
        total=total,
        page=payload.page,
        page_size=payload.page_size,
        summary=SupplierSearchSummary(
            all_count=summary.all_count,
            active_count=summary.active_count,
            inactive_count=summary.inactive_count,
            with_contact_count=summary.with_contact_count,
        ),
    )


@router.post("", response_model=SupplierRead, status_code=status.HTTP_201_CREATED)
async def create_supplier(
    payload: SupplierCreate,
    user: Annotated[CurrentUser, Depends(require_permission("suppliers.create"))],
    service: Annotated[SuppliersService, Depends(_service)],
) -> SupplierRead:
    supplier = await service.create_supplier(
        tenant_id=_current_tenant_or_400(user),
        fields=payload.model_dump(exclude_none=True),
        created_by=user.user_id,
    )
    return SupplierRead.model_validate(supplier)


@router.get("/{supplier_id}", response_model=SupplierRead)
async def get_supplier(
    supplier_id: UUID,
    user: Annotated[CurrentUser, Depends(require_permission("suppliers.view"))],
    service: Annotated[SuppliersService, Depends(_service)],
) -> SupplierRead:
    supplier = await service.get_supplier(
        supplier_id,
        tenant_id=_current_tenant_or_400(user),
    )
    return SupplierRead.model_validate(supplier)


@router.patch("/{supplier_id}", response_model=SupplierRead)
async def update_supplier(
    supplier_id: UUID,
    payload: SupplierUpdate,
    user: Annotated[CurrentUser, Depends(require_permission("suppliers.update"))],
    service: Annotated[SuppliersService, Depends(_service)],
) -> SupplierRead:
    supplier = await service.update_supplier(
        supplier_id,
        tenant_id=_current_tenant_or_400(user),
        fields=payload.model_dump(exclude_unset=True),
        updated_by=user.user_id,
    )
    return SupplierRead.model_validate(supplier)
