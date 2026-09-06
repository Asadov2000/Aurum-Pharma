"""Read-only object storage boundary for catalog import workers."""

from __future__ import annotations

from functools import lru_cache

from minio import Minio

from app.core.catalog_worker_config import get_catalog_worker_settings


@lru_cache
def get_catalog_minio() -> Minio:
    settings = get_catalog_worker_settings()
    return Minio(
        settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=settings.MINIO_SECURE,
    )


def validate_catalog_import_path(
    object_path: str,
    *,
    tenant_id: str,
    expected_bucket: str,
) -> tuple[str, str]:
    bucket, separator, key = object_path.partition("/")
    segments = key.split("/")
    expected_prefix = f"{tenant_id}/imports/"
    if (
        not separator
        or bucket != expected_bucket
        or not key.startswith(expected_prefix)
        or key == expected_prefix
        or "\\" in key
        or any(segment in {"", ".", ".."} for segment in segments)
    ):
        raise ValueError("Catalog import object is outside the tenant import boundary")
    return bucket, key


def get_catalog_object(object_path: str, *, tenant_id: str) -> bytes:
    settings = get_catalog_worker_settings()
    bucket, key = validate_catalog_import_path(
        object_path,
        tenant_id=tenant_id,
        expected_bucket=settings.MINIO_BUCKET,
    )
    response = get_catalog_minio().get_object(bucket, key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()
