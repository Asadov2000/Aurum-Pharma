"""FastAPI endpoints for the incoming domain."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import UUID4
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    CurrentUser,
    get_db,
    require_branch_permission,
    require_recent_account_mfa,
)
from app.core.errors import BusinessRuleError
from app.domains.incoming.draft_service import IncomingDraftService
from app.domains.incoming.models import IncomingDraftRow
from app.domains.incoming.repository import IncomingRepository
from app.domains.incoming.schemas import (
    IncomingDocumentCreate,
    IncomingDocumentList,
    IncomingDocumentRead,
    IncomingDocumentSummary,
    IncomingDocumentUpdate,
    IncomingDocumentWithItems,
    IncomingDraftCommit,
    IncomingDraftRead,
    IncomingDraftReadFields,
    IncomingDraftSave,
    IncomingItemBase,
    IncomingItemRead,
    IncomingItemUpdate,
)
from app.domains.incoming.service import IncomingService

router = APIRouter(prefix="/api/v1/incoming", tags=["incoming"])


class _DraftRoute(APIRoute):
    """Incomplete fields may contain arbitrary private input: never echo it."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        original = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                return await original(request)
            except RequestValidationError as exc:
                known = {
                    "catalog_id",
                    "batch_number",
                    "manufactured_at",
                    "expires_at",
                    "qty",
                    "purchase_price",
                    "sale_price",
                    "expected_revision",
                    "row_id",
                    "document_id",
                }
                fields = sorted(
                    {str(part) for error in exc.errors() for part in error["loc"] if part in known}
                )
                raise BusinessRuleError(
                    "Invalid draft request",
                    details={"reason": "draft_request_invalid", "fields": fields},
                ) from None

        return safe_handler


draft_router = APIRouter(route_class=_DraftRoute)

INCOMING_COST_PERMISSIONS = (
    "batches.view_costs",
    "incoming.create",
    "incoming.finalize",
)


def _can_view_incoming_cost(user: CurrentUser, branch_id: UUID) -> bool:
    scope = user.branch_scope_for_any(*INCOMING_COST_PERMISSIONS)
    return scope is None or branch_id in scope


def _can_view_incoming_summary_cost(
    user: CurrentUser,
    *,
    branch_id: UUID | None,
    visible_branch_scope: set[UUID] | None,
) -> bool:
    cost_scope = user.branch_scope_for_any(*INCOMING_COST_PERMISSIONS)
    if cost_scope is None:
        return True
    if branch_id is not None:
        return branch_id in cost_scope
    return visible_branch_scope is not None and visible_branch_scope.issubset(cost_scope)


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
    user: Annotated[
        CurrentUser,
        Depends(require_branch_permission("incoming.view", policy="filter")),
    ],
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
        return IncomingDocumentList(
            items=[],
            total=0,
            page=page,
            page_size=page_size,
            summary=IncomingDocumentSummary(
                all_count=0,
                draft_count=0,
                accepted_count=0,
                rejected_count=0,
                accepted_amount=0,
            ),
        )
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
    summary = await service.summarize_documents(
        branch_id=branch_id,
        branch_ids=branch_scope,
        supplier_id=supplier_id,
        document_number=document_number,
        date_from=date_from,
        date_to=date_to,
    )
    return IncomingDocumentList(
        items=[
            IncomingDocumentRead.model_validate(row.document).model_copy(
                update={
                    "branch_name": row.branch_name,
                    "supplier_name": row.supplier_name,
                    "total_amount": (
                        row.document.total_amount
                        if _can_view_incoming_cost(user, row.document.branch_id)
                        else None
                    ),
                }
            )
            for row in docs
        ],
        total=total,
        page=page,
        page_size=page_size,
        summary=IncomingDocumentSummary(
            all_count=summary.all_count,
            draft_count=summary.draft_count,
            accepted_count=summary.accepted_count,
            rejected_count=summary.rejected_count,
            accepted_amount=(
                summary.accepted_amount
                if _can_view_incoming_summary_cost(
                    user,
                    branch_id=branch_id,
                    visible_branch_scope=branch_scope,
                )
                else None
            ),
        ),
    )


@router.post(
    "",
    response_model=IncomingDocumentRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_incoming(
    payload: IncomingDocumentCreate,
    user: Annotated[
        CurrentUser,
        Depends(require_branch_permission("incoming.create", policy="direct")),
    ],
    service: Annotated[IncomingService, Depends(_service)],
) -> IncomingDocumentRead:
    doc = await service.create_document(
        tenant_id=_current_tenant_or_400(user),
        fields=payload.model_dump(exclude={"operation_id"}),
        operation_id=payload.operation_id,
        created_by=user.user_id,
        allowed_branch_ids=user.branch_scope_for("incoming.create"),
    )
    return IncomingDocumentRead.model_validate(doc)


@router.get("/{document_id}", response_model=IncomingDocumentWithItems)
async def get_incoming(
    document_id: UUID,
    user: Annotated[
        CurrentUser,
        Depends(require_branch_permission("incoming.view", policy="resource")),
    ],
    service: Annotated[IncomingService, Depends(_service)],
) -> IncomingDocumentWithItems:
    branch_scope = user.branch_scope_for("incoming.view")
    details = await service.get_document_details(document_id, allowed_branch_ids=branch_scope)
    items = await service.list_item_details(document_id, allowed_branch_ids=branch_scope)
    return IncomingDocumentWithItems(
        **IncomingDocumentRead.model_validate(details.document)
        .model_copy(
            update={
                "total_amount": (
                    details.document.total_amount
                    if _can_view_incoming_cost(user, details.document.branch_id)
                    else None
                )
            }
        )
        .model_dump(exclude={"branch_name", "supplier_name"}),
        branch_name=details.branch_name,
        supplier_name=details.supplier_name,
        items=[
            IncomingItemRead(
                **IncomingItemRead.model_validate(item.item)
                .model_copy(
                    update={
                        "purchase_price": (
                            item.item.purchase_price
                            if _can_view_incoming_cost(user, details.document.branch_id)
                            else None
                        )
                    }
                )
                .model_dump(
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
    user: Annotated[
        CurrentUser,
        Depends(require_branch_permission("incoming.create", policy="resource")),
    ],
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
    user: Annotated[
        CurrentUser,
        Depends(require_branch_permission("incoming.create", policy="resource")),
    ],
    service: Annotated[IncomingService, Depends(_service)],
) -> IncomingItemRead:
    item = await service.add_item(
        document_id,
        fields=payload.model_dump(exclude={"operation_id"}),
        operation_id=payload.operation_id,
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
    user: Annotated[
        CurrentUser,
        Depends(require_branch_permission("incoming.create", policy="resource")),
    ],
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
    user: Annotated[
        CurrentUser,
        Depends(require_branch_permission("incoming.create", policy="resource")),
    ],
    service: Annotated[IncomingService, Depends(_service)],
) -> dict[str, str]:
    await service.delete_item(
        document_id,
        item_id,
        allowed_branch_ids=user.branch_scope_for("incoming.create"),
    )
    return {"status": "deleted"}


# ---- accept / reject ----


def _draft_read(
    row: IncomingDraftRow, *, catalog_name: str | None, show_cost: bool
) -> IncomingDraftRead:
    fields = IncomingDraftReadFields.model_validate(row.fields)
    if not show_cost:
        fields.purchase_price = None
    return IncomingDraftRead(
        id=row.id,
        document_id=row.document_id,
        revision=row.revision,
        fields=fields,
        catalog_name=catalog_name,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@draft_router.get("/{document_id}/draft-rows", response_model=list[IncomingDraftRead])
async def list_draft_rows(
    document_id: UUID,
    user: Annotated[
        CurrentUser, Depends(require_branch_permission("incoming.view", policy="resource"))
    ],
    service: Annotated[IncomingService, Depends(_service)],
) -> list[IncomingDraftRead]:
    details, rows, names = await IncomingDraftService(service).list_rows(
        document_id, allowed_branch_ids=user.branch_scope_for("incoming.view")
    )
    return [
        _draft_read(
            row,
            catalog_name=names.get(row.fields["catalog_id"]),
            show_cost=_can_view_incoming_cost(user, details.document.branch_id),
        )
        for row in rows
    ]


@draft_router.put("/{document_id}/draft-rows/{row_id}", response_model=IncomingDraftRead)
async def save_draft_row(
    document_id: UUID,
    row_id: UUID4,
    payload: IncomingDraftSave,
    user: Annotated[
        CurrentUser, Depends(require_branch_permission("incoming.create", policy="resource"))
    ],
    service: Annotated[IncomingService, Depends(_service)],
) -> IncomingDraftRead:
    row = await IncomingDraftService(service).save(
        document_id,
        row_id,
        expected_revision=payload.expected_revision,
        fields=payload.fields,
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("incoming.create"),
    )
    names = await IncomingDraftService(service).catalog_names(row.tenant_id, [row])
    # incoming.create is itself an operational cost capability for this branch.
    return _draft_read(row, catalog_name=names.get(row.fields["catalog_id"]), show_cost=True)


@draft_router.post("/{document_id}/draft-rows/{row_id}/commit", response_model=IncomingItemRead)
async def commit_draft_row(
    document_id: UUID,
    row_id: UUID4,
    payload: IncomingDraftCommit,
    user: Annotated[
        CurrentUser, Depends(require_branch_permission("incoming.create", policy="resource"))
    ],
    service: Annotated[IncomingService, Depends(_service)],
) -> IncomingItemRead:
    item = await IncomingDraftService(service).commit(
        document_id,
        row_id,
        expected_revision=payload.expected_revision,
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("incoming.create"),
    )
    return IncomingItemRead.model_validate(item)


@draft_router.delete("/{document_id}/draft-rows/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def discard_draft_row(
    document_id: UUID,
    row_id: UUID4,
    user: Annotated[
        CurrentUser, Depends(require_branch_permission("incoming.create", policy="resource"))
    ],
    service: Annotated[IncomingService, Depends(_service)],
    expected_revision: Annotated[int, Query(ge=1, le=2_147_483_645)],
) -> Response:
    await IncomingDraftService(service).discard(
        document_id,
        row_id,
        expected_revision=expected_revision,
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("incoming.create"),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


router.include_router(draft_router)


@router.post(
    "/{document_id}/accept",
    response_model=IncomingDocumentRead,
    dependencies=[Depends(require_recent_account_mfa)],
)
async def accept_incoming(
    document_id: UUID,
    user: Annotated[
        CurrentUser,
        Depends(require_branch_permission("incoming.finalize", policy="resource")),
    ],
    service: Annotated[IncomingService, Depends(_service)],
) -> IncomingDocumentRead:
    doc = await service.accept(
        document_id,
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("incoming.finalize"),
    )
    return IncomingDocumentRead.model_validate(doc)


@router.post(
    "/{document_id}/reject",
    response_model=IncomingDocumentRead,
    dependencies=[Depends(require_recent_account_mfa)],
)
async def reject_incoming(
    document_id: UUID,
    user: Annotated[
        CurrentUser,
        Depends(require_branch_permission("incoming.finalize", policy="resource")),
    ],
    service: Annotated[IncomingService, Depends(_service)],
) -> IncomingDocumentRead:
    doc = await service.reject(
        document_id,
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("incoming.finalize"),
    )
    return IncomingDocumentRead.model_validate(doc)
