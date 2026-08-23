from __future__ import annotations

import hashlib
import io
from uuid import uuid4

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.core.config import get_settings
from app.core.errors import ValidationError
from app.domains.catalog.image_processing import process_catalog_image
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.router import _read_catalog_image
from app.domains.catalog.service import CatalogService, new_catalog_image_object_name


def _image_bytes(image_format: str = "JPEG", *, size: tuple[int, int] = (1_600, 900)) -> bytes:
    source = Image.new("RGB", size, "white")
    output = io.BytesIO()
    exif = Image.Exif()
    exif[0x010E] = "private source metadata"
    source.save(output, format=image_format, exif=exif)
    return output.getvalue()


def test_process_catalog_image_normalizes_and_removes_metadata() -> None:
    result = process_catalog_image(_image_bytes(), "image/jpeg")

    assert result.width == 1_200
    assert result.height == 675
    assert result.sha256 == hashlib.sha256(result.display).hexdigest()
    assert len(result.thumbnail) < len(result.display)
    with Image.open(io.BytesIO(result.display)) as display:
        assert display.format == "WEBP"
        assert display.size == (1_200, 675)
        assert len(display.getexif()) == 0
    with Image.open(io.BytesIO(result.thumbnail)) as thumbnail:
        assert thumbnail.format == "WEBP"
        assert max(thumbnail.size) == 320


def test_process_catalog_image_rejects_spoofed_and_invalid_files() -> None:
    with pytest.raises(ValidationError, match="не соответствует"):
        process_catalog_image(_image_bytes("PNG"), "image/jpeg")

    with pytest.raises(ValidationError, match="повреждён"):
        process_catalog_image(b"not an image", "image/jpeg")


async def test_catalog_image_reader_stops_at_limit() -> None:
    upload = UploadFile(filename="large.jpg", file=io.BytesIO(b"123"))
    with pytest.raises(ValidationError, match="больше"):
        await _read_catalog_image(upload, max_bytes=2)


async def test_catalog_image_metadata_replace_and_clear(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    service = CatalogService(CatalogRepository(db_session))
    item = await service.create_item(
        tenant_id=tenant.id,
        fields={"brand_name": "Фото-тест"},
        created_by=user.id,
    )
    image = process_catalog_image(_image_bytes(size=(640, 480)), "image/jpeg")
    first_version = uuid4()
    updated, old_version = await service.set_image_metadata(
        item.id,
        version=first_version,
        image=image,
        updated_by=user.id,
    )
    assert old_version is None
    assert updated.image_version == first_version
    assert updated.image_size_bytes == len(image.display)
    assert updated.image_thumbnail_size_bytes == len(image.thumbnail)
    assert updated.image_sha256 == image.sha256

    second_version = uuid4()
    replaced, old_version = await service.set_image_metadata(
        item.id,
        version=second_version,
        image=image,
        updated_by=user.id,
    )
    assert old_version == first_version
    assert replaced.image_version == second_version

    cleared, old_version = await service.clear_image_metadata(item.id, updated_by=user.id)
    assert old_version == second_version
    assert cleared.image_version is None
    assert cleared.image_sha256 is None


def test_catalog_image_object_name_is_server_generated() -> None:
    tenant_id = uuid4()
    item_id = uuid4()
    version = uuid4()
    assert new_catalog_image_object_name(tenant_id, item_id, version, "thumbnail") == (
        f"{tenant_id}/catalog/{item_id}/images/{version}/thumbnail.webp"
    )


async def test_catalog_image_upload_is_allowed_by_cors(client: AsyncClient) -> None:
    allowed_origin = get_settings().CORS_ORIGINS[0]
    response = await client.options(
        f"/api/v1/catalog/{uuid4()}/image",
        headers={
            "Origin": allowed_origin,
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )

    assert response.status_code == 200
    allowed_methods = response.headers["access-control-allow-methods"].split(", ")
    assert "PUT" in allowed_methods
