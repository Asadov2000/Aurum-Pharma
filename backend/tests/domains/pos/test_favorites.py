"""Personal POS favorites: contract, ordering, and ownership isolation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, current_user, get_db
from app.core.errors import NotFoundError, PermissionDeniedError
from app.domains.auth.models import AppUser
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.domains.pos.models import POSFavorite
from app.domains.pos.repository import POSRepository
from app.domains.pos.schemas import POSFavoriteCreate
from app.domains.pos.service import POSService
from app.main import app


def test_favorite_contract_accepts_deterministic_catalog_uuid() -> None:
    catalog_id = uuid5(NAMESPACE_URL, "aurum-showcase-catalog")

    assert POSFavoriteCreate(catalog_id=catalog_id).catalog_id == catalog_id


async def _make_user(session: AsyncSession, *, tenant_id: UUID) -> AppUser:
    suffix = uuid4().hex[:8]
    user = AppUser(
        email=f"favorite-{suffix}@aurum.tj",
        full_name=f"Favorite User {suffix}",
        home_tenant_id=tenant_id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return user


async def test_personal_favorites_are_idempotent_ordered_and_include_branch_stock(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))

    first = await service.add_favorite(
        tenant_id=scaffold["tenant"].id,
        user_id=scaffold["cashier"].id,
        catalog_id=scaffold["item"].id,
    )
    duplicate = await service.add_favorite(
        tenant_id=scaffold["tenant"].id,
        user_id=scaffold["cashier"].id,
        catalog_id=scaffold["item"].id,
    )
    assert duplicate.id == first.id

    await db_session.execute(
        update(POSFavorite)
        .where(POSFavorite.id == first.id)
        .values(created_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    second_item = await CatalogService(CatalogRepository(db_session)).create_item(
        tenant_id=scaffold["tenant"].id,
        fields={"brand_name": "Favorite without stock", "dispensing_type": "otc"},
    )
    second = await service.add_favorite(
        tenant_id=scaffold["tenant"].id,
        user_id=scaffold["cashier"].id,
        catalog_id=second_item.id,
    )

    rows = await service.list_favorites(
        tenant_id=scaffold["tenant"].id,
        user_id=scaffold["cashier"].id,
        branch_id=scaffold["branch"].id,
        allowed_branch_ids={scaffold["branch"].id},
    )

    assert [row.favorite.id for row in rows] == [second.id, first.id]
    assert rows[0].stock_available == 0
    assert rows[1].stock_available == scaffold["batch"].qty_remaining

    await CatalogRepository(db_session).update_item(scaffold["item"], is_active=False)
    rows = await service.list_favorites(
        tenant_id=scaffold["tenant"].id,
        user_id=scaffold["cashier"].id,
        branch_id=scaffold["branch"].id,
        allowed_branch_ids=None,
    )
    assert rows[1].catalog.is_active is False

    await CatalogRepository(db_session).soft_delete_item(second_item.id)
    rows = await service.list_favorites(
        tenant_id=scaffold["tenant"].id,
        user_id=scaffold["cashier"].id,
        branch_id=scaffold["branch"].id,
        allowed_branch_ids=None,
    )
    assert [row.favorite.id for row in rows] == [first.id]
    assert rows[0].catalog.is_active is False


async def test_favorites_are_isolated_by_user_tenant_and_branch_scope(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    first = await pos_scaffold()
    second = await pos_scaffold()
    other_user = await _make_user(db_session, tenant_id=first["tenant"].id)
    service = POSService(POSRepository(db_session))

    owner_favorite = await service.add_favorite(
        tenant_id=first["tenant"].id,
        user_id=first["cashier"].id,
        catalog_id=first["item"].id,
    )
    other_favorite = await service.add_favorite(
        tenant_id=first["tenant"].id,
        user_id=other_user.id,
        catalog_id=first["item"].id,
    )
    assert owner_favorite.id != other_favorite.id

    await service.remove_favorite(
        tenant_id=first["tenant"].id,
        user_id=first["cashier"].id,
        catalog_id=first["item"].id,
    )
    owner_rows = await service.list_favorites(
        tenant_id=first["tenant"].id,
        user_id=first["cashier"].id,
        branch_id=first["branch"].id,
        allowed_branch_ids=None,
    )
    other_rows = await service.list_favorites(
        tenant_id=first["tenant"].id,
        user_id=other_user.id,
        branch_id=first["branch"].id,
        allowed_branch_ids=None,
    )
    assert owner_rows == []
    assert [row.favorite.id for row in other_rows] == [other_favorite.id]

    with pytest.raises(NotFoundError, match="Catalog item"):
        await service.add_favorite(
            tenant_id=first["tenant"].id,
            user_id=first["cashier"].id,
            catalog_id=second["item"].id,
        )
    with pytest.raises(NotFoundError, match="Branch"):
        await service.list_favorites(
            tenant_id=first["tenant"].id,
            user_id=first["cashier"].id,
            branch_id=second["branch"].id,
            allowed_branch_ids=None,
        )
    with pytest.raises(PermissionDeniedError, match="Branch access denied"):
        await service.list_favorites(
            tenant_id=first["tenant"].id,
            user_id=first["cashier"].id,
            branch_id=first["branch"].id,
            allowed_branch_ids=set(),
        )


async def test_favorites_api_requires_pos_sell_and_returns_only_current_user(
    client: AsyncClient,
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    scaffold = await pos_scaffold()
    actor = CurrentUser(
        user_id=scaffold["cashier"].id,
        tenant_id=scaffold["tenant"].id,
        is_developer=False,
        is_administrator=False,
    )

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_user() -> CurrentUser:
        return actor

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[current_user] = _override_user
    try:
        denied = await client.post(
            "/api/v1/pos/favorites",
            json={"catalog_id": str(scaffold["item"].id)},
        )
        assert denied.status_code == 403

        actor.permissions = {"pos.sell"}
        actor.permission_scopes = {"pos.sell": frozenset({scaffold["branch"].id})}
        created = await client.post(
            "/api/v1/pos/favorites",
            json={"catalog_id": str(scaffold["item"].id)},
        )
        assert created.status_code == 201
        assert created.json()["catalog_id"] == str(scaffold["item"].id)

        response = await client.get(
            "/api/v1/pos/favorites",
            params={"branch_id": str(scaffold["branch"].id)},
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        payload = response.json()
        assert len(payload) == 1
        assert payload[0]["catalog"]["stock_available"] == "100.000"

        actor.user_id = (await _make_user(db_session, tenant_id=scaffold["tenant"].id)).id
        isolated = await client.get(
            "/api/v1/pos/favorites",
            params={"branch_id": str(scaffold["branch"].id)},
        )
        assert isolated.status_code == 200
        assert isolated.json() == []
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(current_user, None)
