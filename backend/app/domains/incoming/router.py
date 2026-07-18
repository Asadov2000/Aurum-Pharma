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


@router.get("", response_model=list[IncomingDocumentRead])
async def list_incoming(
    user: Annotated[CurrentUser, Depends(require_permission("incoming.view"))],
    service: Annotated[IncomingService, Depends(_service)],
    branch_id: Annotated[UUID | None, Query()] = None,
    supplier_id: Annotated[UUID | None, Query()] = None,
    status_q: Annotated[str | None, Query(alias="status")] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> list[IncomingDocumentRead]:
    branch_scope = user.branch_scope_for("incoming.view")
    if branch_scope is not None and branch_id is not None and branch_id not in branch_scope:
        return []
    effective_branch_id = branch_id
    if branch_scope is not None and branch_id is None and len(branch_scope) == 1:
        effective_branch_id = next(iter(branch_scope))
    docs = await service.list_documents(
        branch_id=effective_branch_id,
        supplier_id=supplier_id,
        status=status_q,
        date_from=date_from,
        date_to=date_to,
    )
    if branch_scope is not None:
        docs = [d for d in docs if d.branch_id in branch_scope]
    return [IncomingDocumentRead.model_validate(d) for d in docs]


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
    doc = await service.get_document(document_id, allowed_branch_ids=branch_scope)
    items = await service.list_items(document_id, allowed_branch_ids=branch_scope)
    return IncomingDocumentWithItems(
        **IncomingDocumentRead.model_validate(doc).model_dump(),
        items=[IncomingItemRead.model_validate(i) for i in items],
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
        fields=payload.model_dump(exclude_none=True),
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
        fields=payload.model_dump(exclude_none=True),
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
