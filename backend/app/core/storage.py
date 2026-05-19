"""MinIO object storage helper.

Lazy: the client is created on first call so the app can boot before
MinIO is reachable. `ensure_bucket` is idempotent and runs every upload
(cheap — one HEAD when the bucket already exists).
"""

from __future__ import annotations

import io
from functools import lru_cache

from minio import Minio

from app.core.config import get_settings


@lru_cache
def get_minio() -> Minio:
    settings = get_settings()
    return Minio(
        settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=settings.MINIO_SECURE,
    )


def ensure_bucket(bucket: str | None = None) -> str:
    """Create the bucket if it doesn't exist; return its name."""
    settings = get_settings()
    name = bucket or settings.MINIO_BUCKET
    client = get_minio()
    if not client.bucket_exists(name):
        client.make_bucket(name)
    return name


def put_object(
    *,
    object_name: str,
    data: bytes,
    content_type: str = "application/octet-stream",
) -> str:
    """Upload bytes under the default bucket; return the full object path
    in `bucket/key` form (without scheme)."""
    bucket = ensure_bucket()
    client = get_minio()
    stream = io.BytesIO(data)
    client.put_object(
        bucket,
        object_name,
        stream,
        length=len(data),
        content_type=content_type,
    )
    return f"{bucket}/{object_name}"


def get_object(object_path: str) -> bytes:
    """Fetch bytes by `bucket/key` path (as returned by put_object)."""
    bucket, _, key = object_path.partition("/")
    client = get_minio()
    response = client.get_object(bucket, key)
    try:
        return response.read()  # type: ignore[no-any-return]
    finally:
        response.close()
        response.release_conn()
