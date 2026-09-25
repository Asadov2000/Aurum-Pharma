"""Reusable catalog choices remain tenant-scoped and do not alter products."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, current_user, get_db
from app.core.errors import ConflictError, NotFoundError
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.schemas import CatalogFieldTemplateCreate
from app.domains.catalog.service import CatalogService
from app.main import app


async def test_template_schema_normalizes_text_and_rejects_blank() -> None:
    request = CatalogFieldTemplateCreate(kind="dosage", value="  500   мг  ")
    assert request.value == "500 мг"
    with pytest.raises(ValueError):
        CatalogFieldTemplateCreate(kind="dosage", value="  ")


async def test_templates_are_shared_only_within_tenant(
    db_session: AsyncSession, make_tenant
) -> None:
    tenant = await make_tenant()
    other = await make_tenant()
    service = CatalogService(CatalogRepository(db_session))
    actor = uuid4()
    one = await service.create_field_template(tenant.id, "category", "Витамины", actor)
    await service.create_field_template(other.id, "category", "Антибиотики", actor)

    assert [(row.id, row.value) for row in await service.list_field_templates(tenant.id, None)] == [
        (one.id, "Витамины")
    ]
    assert [row.value for row in await service.list_field_templates(other.id, "category")] == [
        "Антибиотики"
    ]
    with pytest.raises(NotFoundError):
        await service.delete_field_template(other.id, one.id, actor)


async def test_templates_reject_case_duplicate_and_soft_delete(
    db_session: AsyncSession, make_tenant
) -> None:
    tenant = await make_tenant()
    service = CatalogService(CatalogRepository(db_session))
    actor = uuid4()
    item = await service.create_item(
        tenant_id=tenant.id, fields={"brand_name": "A", "dosage": "500 мг"}
    )
    choice = await service.create_field_template(tenant.id, "dosage", "500 мг", actor)
    with pytest.raises(ConflictError):
        await service.create_field_template(tenant.id, "dosage", "500 МГ", actor)
    await service.delete_field_template(tenant.id, choice.id, actor)
    assert await service.list_field_templates(tenant.id, "dosage") == []
    assert (await service.get_item(item.id)).dosage == "500 мг"
    replacement = await service.create_field_template(tenant.id, "dosage", "500 мг", actor)
    assert replacement.id != choice.id


async def test_template_routes_require_tenant_catalog_permissions(
    client: AsyncClient, db_session: AsyncSession, make_tenant
) -> None:
    tenant = await make_tenant()
    actor = CurrentUser(
        user_id=uuid4(),
        tenant_id=tenant.id,
        is_developer=False,
        is_administrator=False,
        permissions={"catalog.view", "catalog.update"},
        permission_scopes={"catalog.view": None, "catalog.update": None},
    )

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_user() -> CurrentUser:
        return actor

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[current_user] = _override_user
    try:
        created = await client.post(
            "/api/v1/catalog/field-templates",
            json={"kind": "pack_size", "value": "20 таблеток"},
        )
        assert created.status_code == 201, created.text
        assert created.json()["value"] == "20 таблеток"
        listed = await client.get("/api/v1/catalog/field-templates?kind=pack_size")
        assert listed.status_code == 200
        assert listed.json() == [created.json()]

        actor.permission_scopes["catalog.update"] = frozenset({uuid4()})
        denied = await client.post(
            "/api/v1/catalog/field-templates",
            json={"kind": "pack_size", "value": "10 таблеток"},
        )
        assert denied.status_code == 403
        actor.permission_scopes["catalog.update"] = None

        duplicate = await client.post(
            "/api/v1/catalog/field-templates",
            json={"kind": "pack_size", "value": "20 ТАБЛЕТОК"},
        )
        assert duplicate.status_code == 409
        deleted = await client.delete(f"/api/v1/catalog/field-templates/{created.json()['id']}")
        assert deleted.status_code == 204
    finally:
        app.dependency_overrides.pop(current_user, None)
        app.dependency_overrides.pop(get_db, None)
