"""HTTP preparation of the cashier's unpaid physical cart; no financial posting."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    CurrentUser,
    get_db,
    require_branch_permission,
    require_writable_tenant,
)
from app.core.errors import BusinessRuleError, NotFoundError
from app.domains.pos.physical_cart_options_service import PhysicalCartOptionsService
from app.domains.pos.physical_cart_schemas import (
    PhysicalCartItemPut,
    PhysicalCartOptionsRead,
    PhysicalCartQuoteRead,
)
from app.domains.pos.unit_cart_quote import UnitCartQuoteService
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_cart_service import UnitCartItemSnapshot, UnitCartService

router = APIRouter(prefix="/sales/{sale_id}/physical-cart")

CartUser = Annotated[CurrentUser, Depends(require_branch_permission("pos.sell", policy="resource"))]


async def _repository(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> UnitCartRepository:
    return UnitCartRepository(db)


CartRepository = Annotated[UnitCartRepository, Depends(_repository)]


async def _cart_scope(
    sale_id: UUID, user: CurrentUser, repository: UnitCartRepository
) -> tuple[UUID, set[UUID]]:
    if user.tenant_id is None:
        raise BusinessRuleError("Request is not scoped to a tenant")
    tenant_id = user.tenant_id
    await repository.assert_actor(tenant_id, user.user_id)
    branch_ids = user.branch_scope_for("pos.sell")
    if branch_ids is None:
        # Internal services require an explicit set even for a tenant-scoped permission.
        # Resolve only this tenant's sale; services still check cashier and live branch access.
        sale = await repository.lock_sale(tenant_id, sale_id)
        if sale is None:
            raise NotFoundError("Sale draft not found")
        branch_ids = {sale.branch_id}
    return tenant_id, branch_ids


@router.get("", response_model=list[UnitCartItemSnapshot])
async def list_physical_cart(
    sale_id: UUID, user: CartUser, repository: CartRepository, response: Response
) -> tuple[UnitCartItemSnapshot, ...]:
    response.headers["Cache-Control"] = "private, no-store"
    tenant_id, branch_ids = await _cart_scope(sale_id, user, repository)
    return await UnitCartService(repository).list_items(
        tenant_id=tenant_id,
        sale_id=sale_id,
        actor_id=user.user_id,
        allowed_branch_ids=branch_ids,
    )


@router.put(
    "/items/{item_id}",
    response_model=UnitCartItemSnapshot,
    dependencies=[Depends(require_writable_tenant)],
)
async def put_physical_cart_item(
    sale_id: UUID,
    item_id: UUID,
    payload: PhysicalCartItemPut,
    user: CartUser,
    repository: CartRepository,
) -> UnitCartItemSnapshot:
    tenant_id, branch_ids = await _cart_scope(sale_id, user, repository)
    return await UnitCartService(repository).upsert_physical_item(
        tenant_id=tenant_id,
        sale_id=sale_id,
        item_id=item_id,
        catalog_id=payload.catalog_id,
        actor_id=user.user_id,
        allowed_branch_ids=branch_ids,
        position=payload.position,
        sale_format=payload.sale_format,
        quantity=payload.quantity,
        discount_amount=payload.discount_amount,
    )


@router.delete(
    "/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_writable_tenant)],
)
async def delete_physical_cart_item(
    sale_id: UUID, item_id: UUID, user: CartUser, repository: CartRepository
) -> Response:
    tenant_id, branch_ids = await _cart_scope(sale_id, user, repository)
    await UnitCartService(repository).remove_item(
        tenant_id=tenant_id,
        sale_id=sale_id,
        item_id=item_id,
        actor_id=user.user_id,
        allowed_branch_ids=branch_ids,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/quote", response_model=PhysicalCartQuoteRead)
async def quote_physical_cart(
    sale_id: UUID, user: CartUser, repository: CartRepository, response: Response
) -> PhysicalCartQuoteRead:
    response.headers["Cache-Control"] = "private, no-store"
    tenant_id, branch_ids = await _cart_scope(sale_id, user, repository)
    quote = await UnitCartQuoteService(repository).quote(
        tenant_id=tenant_id,
        sale_id=sale_id,
        actor_id=user.user_id,
        allowed_branch_ids=branch_ids,
    )
    return PhysicalCartQuoteRead(quote=quote, revision_hash=quote.revision_hash)


@router.get("/options/{catalog_id}", response_model=PhysicalCartOptionsRead)
async def read_physical_cart_options(
    sale_id: UUID,
    catalog_id: UUID,
    user: CartUser,
    repository: CartRepository,
    response: Response,
) -> PhysicalCartOptionsRead:
    response.headers["Cache-Control"] = "private, no-store"
    tenant_id, branch_ids = await _cart_scope(sale_id, user, repository)
    return await PhysicalCartOptionsService(repository).read(
        tenant_id=tenant_id,
        sale_id=sale_id,
        catalog_id=catalog_id,
        actor_id=user.user_id,
        allowed_branch_ids=branch_ids,
    )
