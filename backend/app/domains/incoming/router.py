"""FastAPI endpoints for the incoming domain."""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_permission
from app.core.errors import BusinessRuleError
from app.domains.incoming.repository import IncomingRepository
from app.domains.incoming.schemas import (
    IncomingDocumentCreate,
    IncomingDocumentList,
    IncomingDocumentRead,
    IncomingDocumentUpdate,
    IncomingDocumentWithItems,
    IncomingItemBase,
    IncomingItemRead,
    IncomingItemUpdate,
)
from app.domains.incoming.service import IncomingService

router = APIRouter(prefix="/api/v1/incoming", tags=["incoming"])


async def _service(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> IncomingService:
    return IncomingService(IncomingRepository(db))


def _current_tenant_or_400(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise BusinessRuleError("Request is not scoped to a tenant")
    return user.tenant_id


# ---- documents ----


@router.get("", response_model=IncomingDocumentList)
async def list_incoming(
    user: Annotated[CurrentUser, Depends(require_permission("incoming.view"))],
    service: Annotated[IncomingService, Depends(_service)],
    branch_id: Annotated[UUID | None, Query()] = None,
    supplier_id: Annotated[UUID | None, Query()] = None,
    status_q: Annotated[str | None, Query(alias="status")] = None,
    document_number: Annotated[str | None, Query(max_length=100)] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> IncomingDocumentList:
    branch_scope = user.branch_scope_for("incoming.view")
    if branch_scope is not None and branch_id is not None and branch_id not in branch_scope:
        return IncomingDocumentList(items=[], total=0, page=page, page_size=page_size)
    docs, total = await service.list_documents(
        branch_id=branch_id,
        branch_ids=branch_scope,
        supplier_id=supplier_id,
        status=status_q,
        document_number=document_number,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )
    return IncomingDocumentList(
        items=[IncomingDocumentRead.model_validate(d) for d in docs],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "",
    response_model=IncomingDocumentRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_incoming(
    payload: IncomingDocumentCreate,
    user: Annotated[CurrentUser, Depends(require_permission("incoming.create"))],
    service: Annotated[IncomingService, Depends(_service)],
) -> IncomingDocumentRead:
    doc = await service.create_document(
        tenant_id=_current_tenant_or_400(user),
        fields=payload.model_dump(),
        created_by=user.user_id,
        allowed_branch_ids=user.branch_scope_for("incoming.create"),
    )
    return IncomingDocumentRead.model_validate(doc)


@router.get("/{document_id}", response_model=IncomingDocumentWithItems)
async def get_incoming(
    document_id: UUID,
    user: Annotated[CurrentUser, Depends(require_permission("incoming.view"))],
    service: Annotated[IncomingService, Depends(_service)],
) -> IncomingDocumentWithItems:
    branch_scope = user.branch_scope_for("incoming.view")
    details = await service.get_document_details(document_id, allowed_branch_ids=branch_scope)
    items = await service.list_item_details(document_id, allowed_branch_ids=branch_scope)
    return IncomingDocumentWithItems(
        **IncomingDocumentRead.model_validate(details.document).model_dump(
            exclude={"branch_name", "supplier_name"}
        ),
        branch_name=details.branch_name,
        supplier_name=details.supplier_name,
        items=[
            IncomingItemRead(
                **IncomingItemRead.model_validate(item.item).model_dump(
                    exclude={
                        "catalog_name",
                        "catalog_form",
                        "catalog_dosage",
                        "catalog_pack_size",
                    }
                ),
                catalog_name=item.catalog_name,
                catalog_form=item.catalog_form,
                catalog_dosage=item.catalog_dosage,
                catalog_pack_size=item.catalog_pack_size,
            )
            for item in items
        ],
    )


@router.patch("/{document_id}", response_model=IncomingDocumentRead)
async def update_incoming(
    document_id: UUID,
    payload: IncomingDocumentUpdate,
    user: Annotated[CurrentUser, Depends(require_permission("incoming.create"))],
    service: Annotated[IncomingService, Depends(_service)],
) -> IncomingDocumentRead:
    doc = await service.update_document(
        document_id,
        fields=payload.model_dump(exclude_unset=True),
        updated_by=user.user_id,
        allowed_branch_ids=user.branch_scope_for("incoming.create"),
    )
    return IncomingDocumentRead.model_validate(doc)


# ---- items ----


@router.post(
    "/{document_id}/items",
    response_model=IncomingItemRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_item(
    document_id: UUID,
    payload: IncomingItemBase,
    user: Annotated[CurrentUser, Depends(require_permission("incoming.create"))],
    service: Annotated[IncomingService, Depends(_service)],
) -> IncomingItemRead:
    item = await service.add_item(
        document_id,
        fields=payload.model_dump(),
        allowed_branch_ids=user.branch_scope_for("incoming.create"),
    )
    return IncomingItemRead.model_validate(item)


@router.patch(
    "/{document_id}/items/{item_id}",
    response_model=IncomingItemRead,
)
async def update_item(
    document_id: UUID,
    item_id: UUID,
    payload: IncomingItemUpdate,
    user: Annotated[CurrentUser, Depends(require_permission("incoming.create"))],
    service: Annotated[IncomingService, Depends(_service)],
) -> IncomingItemRead:
    item = await service.update_item(
        document_id,
        item_id,
        fields=payload.model_dump(exclude_unset=True),
        allowed_branch_ids=user.branch_scope_for("incoming.create"),
    )
    return IncomingItemRead.model_validate(item)


@router.delete("/{document_id}/items/{item_id}")
async def delete_item(
    document_id: UUID,
    item_id: UUID,
    user: Annotated[CurrentUser, Depends(require_permission("incoming.create"))],
    service: Annotated[IncomingService, Depends(_service)],
) -> dict[str, str]:
    await service.delete_item(
        document_id,
        item_id,
        allowed_branch_ids=user.branch_scope_for("incoming.create"),
    )
    return {"status": "deleted"}


# ---- accept / reject ----


@router.post("/{document_id}/accept", response_model=IncomingDocumentRead)
async def accept_incoming(
    document_id: UUID,
    user: Annotated[CurrentUser, Depends(require_permission("incoming.create"))],
    service: Annotated[IncomingService, Depends(_service)],
) -> IncomingDocumentRead:
    doc = await service.accept(
        document_id,
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("incoming.create"),
    )
    return IncomingDocumentRead.model_validate(doc)


@router.post("/{document_id}/reject", response_model=IncomingDocumentRead)
async def reject_incoming(
    document_id: UUID,
    user: Annotated[CurrentUser, Depends(require_permission("incoming.create"))],
    service: Annotated[IncomingService, Depends(_service)],
) -> IncomingDocumentRead:
    doc = await service.reject(
        document_id,
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("incoming.create"),
    )
    return IncomingDocumentRead.model_validate(doc)
