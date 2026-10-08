"""Private photo responses authorize every request using real runtime-role SQL."""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import deps
from app.core.config import get_settings
from app.core.security import create_access_token, hash_token
from app.core.storage import default_object_path
from app.core.time import utc_now
from app.domains.auth.models import AppUser, Session
from app.domains.catalog import router as catalog_router
from app.domains.catalog.image_processing import process_catalog_image
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService, new_catalog_image_object_name
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.roles.models import TenantMembership
from app.main import app
from tests.role_version_helpers import provision_test_owner


@dataclass
class PhotoFixture:
    item_id: UUID
    version: UUID
    owner_token: str = field(repr=False)
    foreign_token: str = field(repr=False)
    employee_token: str = field(repr=False)
    objects: dict[str, bytes]
    reads: list[str] = field(default_factory=list)

    def url(self, variant: str, *, version: UUID | None = None) -> str:
        return f"/api/v1/catalog/{self.item_id}/image/{version or self.version}/{variant}"


@pytest_asyncio.fixture
async def photo_fixture(
    db_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[PhotoFixture]:
    # conftest validates disposable database and Redis URLs before app imports.
    # Keep each request on its own real aurum_app connection: no get_db override.
    app_engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    monkeypatch.setattr(deps, "AppSessionLocal", async_sessionmaker(app_engine))
    monkeypatch.setattr(deps, "SupportSessionLocal", async_sessionmaker(db_engine))
    assert deps.get_db not in app.dependency_overrides
    subjects: list[tuple[UUID, UUID, UUID]] = []
    objects: dict[str, bytes] = {}
    version = uuid4()
    try:
        async with async_sessionmaker(app_engine)() as runtime_db:
            assert await runtime_db.scalar(text("SELECT session_user")) == "aurum_app"
        async with async_sessionmaker(db_engine, expire_on_commit=False).begin() as db:
            for index in range(2):
                await db.execute(
                    text(
                        "SELECT set_config('app.support_session','true',true), "
                        "set_config('app.tenant_id','',true)"
                    )
                )
                tenant = await FoundationService(FoundationRepository(db)).create_tenant(
                    payload={
                        "name": f"Photo fixture {uuid4().hex}",
                        "contact_email": f"photo-{uuid4().hex}@example.invalid",
                    }
                )
                owner, _, _, _ = await provision_test_owner(
                    db,
                    tenant_id=tenant.id,
                    email=f"photo-owner-{uuid4().hex}@example.invalid",
                    full_name="Photo fixture owner",
                )
                auth_session = Session(
                    user_id=owner.id,
                    refresh_token_hash=hash_token(f"photo-session-{uuid4()}"),
                    expires_at=utc_now() + timedelta(hours=1),
                )
                db.add(auth_session)
                await db.flush()
                subjects.append((tenant.id, owner.id, auth_session.id))
                if index == 0:
                    service = CatalogService(CatalogRepository(db))
                    item = await service.create_item(
                        tenant_id=tenant.id,
                        fields={"brand_name": "Private photo fixture"},
                        created_by=owner.id,
                    )
                    image_file = io.BytesIO()
                    Image.new("RGB", (64, 48), "white").save(image_file, format="PNG")
                    image = process_catalog_image(image_file.getvalue(), "image/png")
                    await service.set_image_metadata(
                        item.id, version=version, image=image, updated_by=owner.id
                    )
                    item_id = item.id
                    for variant, content in (
                        ("display", image.display),
                        ("thumbnail", image.thumbnail),
                    ):
                        path = default_object_path(
                            new_catalog_image_object_name(tenant.id, item.id, version, variant)
                        )
                        objects[path] = content
                    employee = AppUser(
                        email=f"photo-employee-{uuid4().hex}@example.invalid",
                        full_name="Employee without catalog access",
                        home_tenant_id=tenant.id,
                        status="active",
                    )
                    db.add(employee)
                    await db.flush()
                    db.add(
                        TenantMembership(
                            tenant_id=tenant.id,
                            user_id=employee.id,
                            full_name=employee.full_name,
                            status="active",
                        )
                    )
                    employee_session = Session(
                        user_id=employee.id,
                        refresh_token_hash=hash_token(f"employee-photo-session-{uuid4()}"),
                        expires_at=utc_now() + timedelta(hours=1),
                    )
                    db.add(employee_session)
                    await db.flush()
                    employee_subject = (tenant.id, employee.id, employee_session.id)

        def token(subject: tuple[UUID, UUID, UUID]) -> str:
            tenant_id, user_id, session_id = subject
            return create_access_token(
                user_id,
                tenant_id=tenant_id,
                session_id=session_id,
                is_developer=False,
                is_administrator=False,
            )

        fixture = PhotoFixture(
            item_id=item_id,
            version=version,
            owner_token=token(subjects[0]),
            foreign_token=token(subjects[1]),
            employee_token=token(employee_subject),
            objects=objects,
        )

        def read(path: str) -> bytes:
            fixture.reads.append(path)
            return objects[path]

        monkeypatch.setattr(catalog_router, "get_object", read)
        yield fixture
    finally:
        await app_engine.dispose()


@pytest.mark.parametrize("variant", ["display", "thumbnail"])
async def test_private_photo_reauthorizes_conditional_requests(
    client: AsyncClient, photo_fixture: PhotoFixture, variant: str
) -> None:
    fixture = photo_fixture
    url = fixture.url(variant)
    response = await client.get(url, headers={"Authorization": f"Bearer {fixture.owner_token}"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    assert response.headers["cache-control"] == "private, no-store"
    assert {part.strip().lower() for part in response.headers["vary"].split(",")} == {
        "authorization",
        "x-aurum-support-session",
    }
    assert response.headers["x-content-type-options"] == "nosniff"
    with Image.open(io.BytesIO(response.content)) as image:
        assert image.format == "WEBP"
    assert len(fixture.reads) == 1
    assert response.content == fixture.objects[fixture.reads[0]]
    etag = response.headers["etag"]
    for token, expected in (
        (fixture.foreign_token, 404),
        (fixture.employee_token, 403),
        (None, 401),
    ):
        headers = {"If-None-Match": etag}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        denied = await client.get(url, headers=headers)
        assert denied.status_code == expected
        assert denied.content != response.content
        assert len(fixture.reads) == 1
    # Even an authorized conditional request reads current bytes, never public 304.
    own_again = await client.get(
        url, headers={"Authorization": f"Bearer {fixture.owner_token}", "If-None-Match": etag}
    )
    assert own_again.status_code == 200
    assert own_again.content == response.content
    assert len(fixture.reads) == 2


async def test_private_photo_stale_version_does_not_read_storage(
    client: AsyncClient, photo_fixture: PhotoFixture
) -> None:
    response = await client.get(
        photo_fixture.url("display", version=uuid4()),
        headers={"Authorization": f"Bearer {photo_fixture.owner_token}"},
    )
    assert response.status_code == 404
    assert photo_fixture.reads == []
