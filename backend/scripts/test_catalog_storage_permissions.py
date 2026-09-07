"""Exercise the provisioned catalog-reader IAM with raw MinIO SDK calls.

Run only through scripts/test-catalog-storage-permissions.ps1 against its fresh
MinIO container. No application path validation, database or real data is used.
Secrets are read from the environment; output contains check names/counts only.
"""

from __future__ import annotations

import argparse
import io
import json
import os
from collections.abc import Callable

from minio import Minio, MinioAdmin
from minio.credentials.providers import StaticProvider
from minio.error import MinioAdminException, S3Error
from urllib3 import PoolManager, Timeout


def read_object(client: Minio, bucket: str, key: str) -> bytes:
    response = client.get_object(bucket, key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def expect_denied(label: str, operation: Callable[[], object]) -> None:
    try:
        operation()
    except S3Error as error:
        if error.code != "AccessDenied":
            raise AssertionError(f"{label}: expected IAM AccessDenied") from None
    else:
        raise AssertionError(f"{label}: catalog reader unexpectedly has permission")
    print(json.dumps({"check": label, "result": "denied"}))


def expect_admin_denied(admin: MinioAdmin) -> None:
    try:
        admin.info()
    except MinioAdminException as error:
        # MinioAdminException has no public status property. Never emit its body.
        message = str(error)
        if not message.startswith("admin request failed; Status: 403,"):
            raise AssertionError("admin_info: expected HTTP 403") from None
        if "AccessDenied" not in message:
            raise AssertionError("admin_info: expected IAM AccessDenied") from None
    else:
        raise AssertionError("admin_info: catalog reader unexpectedly has permission")
    print(json.dumps({"check": "admin_info", "result": "denied"}))


def main(*, verify_only: bool) -> None:
    endpoint = os.environ["MINIO_ENDPOINT"]
    bucket = os.environ["MINIO_BUCKET"]
    # Refuse an existing shared/demo endpoint, even when passed real credentials.
    if (
        os.environ.get("CATALOG_STORAGE_TEST_ISOLATED") != "true"
        or not endpoint.startswith("aurum-catalog-iam-test-")
        or not bucket.startswith("aurum-catalog-iam-test-")
    ):
        raise ValueError("Use the isolated catalog storage test launcher")
    transport = PoolManager(timeout=Timeout(connect=5, read=10), retries=False)
    root = Minio(
        endpoint,
        access_key=os.environ["MINIO_ROOT_USER"],
        secret_key=os.environ["MINIO_ROOT_PASSWORD"],
        region="us-east-1",
        secure=False,
        http_client=transport,
    )
    reader = Minio(
        endpoint,
        access_key=os.environ["CATALOG_MINIO_ACCESS_KEY"],
        secret_key=os.environ["CATALOG_MINIO_SECRET_KEY"],
        region="us-east-1",
        secure=False,
        http_client=transport,
    )
    reader_admin = MinioAdmin(
        endpoint=endpoint,
        credentials=StaticProvider(
            os.environ["CATALOG_MINIO_ACCESS_KEY"],
            os.environ["CATALOG_MINIO_SECRET_KEY"],
        ),
        region="us-east-1",
        secure=False,
        http_client=transport,
    )
    root_admin = MinioAdmin(
        endpoint=endpoint,
        credentials=StaticProvider(
            os.environ["MINIO_ROOT_USER"], os.environ["MINIO_ROOT_PASSWORD"]
        ),
        region="us-east-1",
        secure=False,
        http_client=transport,
    )
    root_admin.info()  # Establish that the admin endpoint itself is operational.
    foreign_bucket = f"{bucket}-other"
    fixtures = [
        (bucket, "tenant-a/imports/catalog.csv", b"brand_name\nSynthetic A\n"),
        (bucket, "tenant-b/imports/catalog.csv", b"brand_name\nSynthetic B\n"),
        (bucket, "tenant-a/images/catalog.csv", b"synthetic private image"),
        (foreign_bucket, "tenant-a/imports/catalog.csv", b"synthetic other bucket"),
    ]
    if not verify_only:
        root.make_bucket(foreign_bucket)
        for fixture_bucket, key, payload in fixtures:
            root.put_object(fixture_bucket, key, io.BytesIO(payload), len(payload))
    for fixture_bucket, key, payload in fixtures[:2]:
        if read_object(reader, fixture_bucket, key) != payload:
            raise AssertionError("allowed_import_get: returned bytes differ")
    print(json.dumps({"check": "allowed_import_get", "result": "passed", "objects": 2}))

    import_key = fixtures[0][1]
    new_key = "tenant-a/imports/unauthorized.csv"
    replacement = b"unauthorized mutation"
    operations: list[tuple[str, Callable[[], object]]] = [
        ("get_other_prefix", lambda: read_object(reader, bucket, fixtures[2][1])),
        ("get_other_bucket", lambda: read_object(reader, foreign_bucket, import_key)),
        (
            "overwrite_import",
            lambda: reader.put_object(
                bucket, import_key, io.BytesIO(replacement), len(replacement)
            ),
        ),
        (
            "create_import",
            lambda: reader.put_object(bucket, new_key, io.BytesIO(replacement), len(replacement)),
        ),
        ("delete_import", lambda: reader.remove_object(bucket, import_key)),
        (
            "list_imports",
            lambda: list(reader.list_objects(bucket, prefix="tenant-a/imports/", recursive=True)),
        ),
        ("list_buckets", reader.list_buckets),
    ]
    for label, operation in operations:
        expect_denied(label, operation)
    expect_admin_denied(reader_admin)

    for fixture_bucket, key, payload in fixtures:
        if read_object(root, fixture_bucket, key) != payload:
            raise AssertionError("root_integrity: fixture was changed or deleted")
    names = {item.object_name for item in root.list_objects(bucket, recursive=True)}
    if names != {key for fixture_bucket, key, _ in fixtures if fixture_bucket == bucket}:
        raise AssertionError("root_integrity: unexpected object created")
    print(json.dumps({"check": "root_integrity", "result": "passed", "objects": 4}))
    print(json.dumps({"allowed_reads": 2, "denied_operations": 8, "integrity": "passed"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    arguments = parser.parse_args()
    try:
        main(verify_only=arguments.verify_only)
    except Exception as error:
        # SDK exceptions can contain request details. Do not print credentials or payloads.
        print(json.dumps({"result": "failed", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
