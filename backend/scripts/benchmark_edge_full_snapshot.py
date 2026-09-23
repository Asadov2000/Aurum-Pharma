"""Measure signed Edge snapshot installation and the first verified local read.

Run only through ``scripts/benchmark-edge-read-model.ps1 -FullSnapshot``. The
launcher provisions a disposable, isolated database; the imported connection
guard refuses all other database identities before opening a connection.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import hashlib
import json
import os
import resource
import secrets
import statistics
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", secrets.token_hex(32))

from app.core.config import get_settings
from app.domains.sync.credentials import issue_edge_credential
from app.domains.sync.read_model import LocalReadModel, _verified_source_cache
from app.domains.sync.read_snapshot import (
    ReadSnapshotScope,
    build_manifest,
    verify_chunk,
    verify_manifest,
)
from app.domains.sync.read_snapshot_projection import (
    BarcodeSnapshot,
    CatalogProductRecord,
    FoundationRecord,
    IndexedReadProjection,
    InventoryBatchRecord,
    build_component_chunks,
    build_indexed_projection,
)
from app.domains.sync.repository import SyncEdgeRepository
from scripts.benchmark_edge_read_model import (
    BRANCH_ID,
    TENANT_ID,
    Connections,
    connections,
    foundation_payload,
    require,
    session,
    verify_environment,
)


def _id(prefix: str, index: int) -> UUID:
    return UUID(hashlib.md5(f"{prefix}-{index}".encode(), usedforsecurity=False).hexdigest())


def _peak_rss_mb() -> float:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


def _current_rss_mb() -> float:
    for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
        if line.startswith("VmRSS:"):
            return round(int(line.split()[1]) / 1024, 1)
    raise RuntimeError("Linux container did not expose process RSS")


def _projection(catalog_rows: int, inventory_rows: int) -> IndexedReadProjection:
    now = datetime.now(UTC)
    today = now.date()
    products = tuple(
        CatalogProductRecord(
            id=_id("catalog", index),
            tenant_id=TENANT_ID,
            brand_name=f"Synthetic product {index}",
            inn=None,
            manufacturer="Synthetic",
            form="tablet",
            dosage="500 mg",
            pack_size="10",
            atx_code=None,
            dispensing_type="otc",
            storage_type="normal",
            category="benchmark",
            base_price=Decimal("12.50"),
            currency="TJS",
            image_version=None,
            is_active=True,
            created_at=now,
            updated_at=now,
            deleted_at=None,
            barcodes=(
                BarcodeSnapshot(
                    id=_id("barcode", index),
                    code=f"29{index:011d}",
                    code_type="ean13",
                ),
            ),
        )
        for index in range(1, catalog_rows + 1)
    )
    batches = tuple(
        InventoryBatchRecord(
            id=_id("batch", index),
            tenant_id=TENANT_ID,
            branch_id=BRANCH_ID,
            catalog_id=_id("catalog", ((index - 1) % catalog_rows) + 1),
            batch_number=f"LOT-{index}",
            manufactured_at=None,
            expires_at=today + timedelta(days=(index % 730) + 1),
            purchase_price=Decimal("8.00"),
            sale_price=Decimal("12.50"),
            currency="TJS",
            qty_initial=Decimal("10.000"),
            qty_remaining=Decimal("0.000" if index % 10 == 0 else "10.000"),
            is_blocked=index % 97 == 0,
            block_reason=None,
            blocked_at=None,
            created_at=now,
            updated_at=now,
            branch_name="Synthetic branch",
            catalog_name=f"Synthetic product {((index - 1) % catalog_rows) + 1}",
            catalog_form="tablet",
            catalog_dosage="500 mg",
            catalog_pack_size="10",
        )
        for index in range(1, inventory_rows + 1)
    )
    return IndexedReadProjection(
        foundation=FoundationRecord.model_validate(foundation_payload()),
        catalog=products,
        suppliers=(),
        inventory=batches,
    )


async def _scope(env: Connections, credential_kid: UUID, credential_digest: str) -> UUID:
    async with session(env.support, support=True) as db:
        await db.execute(
            text(
                "INSERT INTO tenant (id,name,contact_email,status) "
                "VALUES (:tenant_id,'Synthetic Edge benchmark',"
                "'benchmark@example.invalid','active')"
            ),
            {"tenant_id": TENANT_ID},
        )
        await db.execute(
            text(
                "INSERT INTO branch (id,tenant_id,name) VALUES (:branch_id,:tenant_id,'Synthetic branch')"
            ),
            {"tenant_id": TENANT_ID, "branch_id": BRANCH_ID},
        )
        cloud_node_id = await db.scalar(
            text(
                "SELECT writer_node_id FROM sync_stream "
                "WHERE tenant_id=:tenant_id AND branch_id=:branch_id"
            ),
            {"tenant_id": TENANT_ID, "branch_id": BRANCH_ID},
        )
        require(isinstance(cloud_node_id, UUID), 41)
        edge_node_id = await db.scalar(
            text(
                "INSERT INTO sync_node (tenant_id,branch_id,node_kind,mode,status,display_name,"
                "credential_kid,credential_hash,credential_issued_at,credential_expires_at,"
                "shadow_start_origin_node_id,shadow_start_writer_epoch) VALUES "
                "(:tenant_id,:branch_id,'edge','shadow_readonly','active','Benchmark Edge',"
                ":credential_kid,:credential_hash,now(),now()+interval '1 day',:cloud_node_id,1) "
                "RETURNING id"
            ),
            {
                "tenant_id": TENANT_ID,
                "branch_id": BRANCH_ID,
                "credential_kid": credential_kid,
                "credential_hash": credential_digest,
                "cloud_node_id": cloud_node_id,
            },
        )
        require(isinstance(edge_node_id, UUID), 42)
        return edge_node_id


async def _benchmark(
    env: Connections, *, catalog_rows: int, inventory_rows: int, repetitions: int
) -> dict[str, object]:
    credential = issue_edge_credential()
    os.environ["EDGE_SYNC_CREDENTIAL"] = credential.token
    os.environ["EDGE_READ_SNAPSHOT_STALE_SECONDS"] = "86400"
    get_settings.cache_clear()
    await verify_environment(env)
    node_id = await _scope(env, credential.kid, credential.digest)

    started = time.perf_counter()
    projection = _projection(catalog_rows, inventory_rows)
    generation_seconds = time.perf_counter() - started
    generated_rss_mb = _current_rss_mb()

    started = time.perf_counter()
    chunks_by_component = build_component_chunks(
        foundation=projection.foundation,
        catalog=projection.catalog,
        suppliers=projection.suppliers,
        inventory=projection.inventory,
        chunk_size=get_settings().EDGE_READ_SNAPSHOT_CHUNK_SIZE,
    )
    del projection
    gc.collect()
    envelope, chunks = build_manifest(
        ReadSnapshotScope(
            edge_node_id=node_id,
            tenant_id=TENANT_ID,
            branch_id=BRANCH_ID,
            source_revision=1,
        ),
        chunks_by_component,
        credential_kid=credential.kid,
        credential_digest=credential.digest,
        source_generated_at=datetime.now(UTC),
    )
    del chunks_by_component
    gc.collect()
    manifest = verify_manifest(envelope, credential=credential.token)
    for chunk in chunks:
        verify_chunk(manifest, chunk)
    parsed = build_indexed_projection(tenant_id=TENANT_ID, branch_id=BRANCH_ID, chunks=chunks)
    require(len(parsed.catalog) == catalog_rows and len(parsed.inventory) == inventory_rows, 43)
    sign_verify_seconds = time.perf_counter() - started
    signed_rss_mb = _current_rss_mb()

    started = time.perf_counter()
    async with session(
        env.installer, tenant_id=TENANT_ID, branch_id=BRANCH_ID, node_id=node_id
    ) as db:
        await SyncEdgeRepository(db).install_read_snapshot(
            envelope=envelope, chunks=chunks, projection=parsed
        )
    install_seconds = time.perf_counter() - started
    installed_rss_mb = _current_rss_mb()

    chunk_count = len(chunks)
    del chunks, parsed, envelope, manifest
    gc.collect()
    reader_start_rss_mb = _current_rss_mb()

    _verified_source_cache.clear()
    started = time.perf_counter()
    async with session(
        env.app, tenant_id=TENANT_ID, branch_id=BRANCH_ID, node_id=node_id, readonly=True
    ) as db:
        result = await LocalReadModel(SyncEdgeRepository(db)).catalog(
            tenant_id=TENANT_ID, branch_id=BRANCH_ID, query=None, page=1, page_size=20
        )
    first_read_seconds = time.perf_counter() - started
    require(result.total == catalog_rows and len(result.items) == min(20, catalog_rows), 44)
    warm_read_ms: list[float] = []
    for _ in range(repetitions):
        started = time.perf_counter()
        async with session(
            env.app, tenant_id=TENANT_ID, branch_id=BRANCH_ID, node_id=node_id, readonly=True
        ) as db:
            warm_result = await LocalReadModel(SyncEdgeRepository(db)).catalog(
                tenant_id=TENANT_ID, branch_id=BRANCH_ID, query=None, page=1, page_size=20
            )
        require(warm_result.total == catalog_rows, 45)
        warm_read_ms.append((time.perf_counter() - started) * 1000)
    warm_read_ms.sort()
    warm_p95_index = min(len(warm_read_ms) - 1, int(len(warm_read_ms) * 0.95))
    return {
        "status": "success",
        "benchmark": "edge_full_snapshot",
        "catalog_rows": catalog_rows,
        "inventory_rows": inventory_rows,
        "chunks": chunk_count,
        "generation_seconds": round(generation_seconds, 3),
        "sign_verify_seconds": round(sign_verify_seconds, 3),
        "install_seconds": round(install_seconds, 3),
        "first_verified_read_seconds": round(first_read_seconds, 3),
        "warm_read_p50_ms": round(statistics.median(warm_read_ms), 1),
        "warm_read_p95_ms": round(warm_read_ms[warm_p95_index], 1),
        "peak_rss_mb": _peak_rss_mb(),
        "rss_after_generation_mb": generated_rss_mb,
        "rss_after_signing_mb": signed_rss_mb,
        "rss_after_install_mb": installed_rss_mb,
        "rss_before_first_read_mb": reader_start_rss_mb,
        "acceptance_claimed": False,
    }


async def _run(args: argparse.Namespace) -> None:
    require(args.catalog_rows >= 1 and args.inventory_rows >= 1 and args.repetitions >= 5, 40)
    env = connections()
    try:
        print(
            json.dumps(
                await _benchmark(
                    env,
                    catalog_rows=args.catalog_rows,
                    inventory_rows=args.inventory_rows,
                    repetitions=args.repetitions,
                )
            )
        )
    finally:
        await env.app.dispose()
        await env.installer.dispose()
        await env.support.dispose()
        get_settings.cache_clear()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog-rows", type=int, required=True)
    parser.add_argument("--inventory-rows", type=int, required=True)
    parser.add_argument("--repetitions", type=int, required=True)
    asyncio.run(_run(parser.parse_args()))
