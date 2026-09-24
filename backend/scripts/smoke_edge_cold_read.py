"""Seed/probe a disposable Edge read snapshot; only the isolated launcher may run this.

The connection guard is inherited from benchmark_edge_read_model and rejects
every database destination except the launcher-owned internal PostgreSQL alias.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
from uuid import UUID

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.errors import ServiceUnavailableError
from app.domains.sync.credentials import parse_edge_credential
from app.domains.sync.read_model import LocalReadModel
from app.domains.sync.read_snapshot import ReadSnapshotScope, build_manifest
from app.domains.sync.read_snapshot_projection import (
    build_component_chunks,
    build_indexed_projection,
)
from app.domains.sync.readiness import EdgeSnapshotReadiness
from app.domains.sync.repository import SyncEdgeRepository
from scripts.benchmark_edge_full_snapshot import _projection, _scope
from scripts.benchmark_edge_read_model import (
    BRANCH_ID,
    TENANT_ID,
    Connections,
    connections,
    require,
    session,
    verify_environment,
)


async def seed(env: Connections) -> None:
    credential = parse_edge_credential(os.environ["EDGE_SYNC_CREDENTIAL"])
    await verify_environment(env)
    node_id = await _scope(env, credential.kid, credential.digest)
    projection = _projection(3, 5)
    chunks_by_component = build_component_chunks(
        foundation=projection.foundation,
        catalog=projection.catalog,
        suppliers=projection.suppliers,
        inventory=projection.inventory,
        chunk_size=get_settings().EDGE_READ_SNAPSHOT_CHUNK_SIZE,
    )
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
    parsed = build_indexed_projection(
        tenant_id=TENANT_ID, branch_id=BRANCH_ID, chunks=chunks
    )
    async with session(
        env.installer, tenant_id=TENANT_ID, branch_id=BRANCH_ID, node_id=node_id
    ) as db:
        await SyncEdgeRepository(db).install_read_snapshot(
            envelope=envelope, chunks=chunks, projection=parsed
        )
    print(json.dumps({"phase": "seed", "status": "ok", "catalog": 3, "inventory": 5}))


async def read(env: Connections) -> None:
    credential = parse_edge_credential(os.environ["EDGE_SYNC_CREDENTIAL"])
    readiness = EdgeSnapshotReadiness()
    node_id = await _node_id(env, credential.kid)
    async with session(
        env.app,
        tenant_id=TENANT_ID,
        branch_id=BRANCH_ID,
        node_id=node_id,
        readonly=True,
    ) as db:
        model = LocalReadModel(
            SyncEdgeRepository(db), readiness_guard=readiness.require_verified
        )
        try:
            await model.catalog(
                tenant_id=TENANT_ID,
                branch_id=BRANCH_ID,
                query=None,
                page=1,
                page_size=20,
            )
        except ServiceUnavailableError:
            pass
        else:
            raise AssertionError("Unverified snapshot was served")
    await readiness.poll_once()
    require(readiness.is_ready, 71)
    async with session(
        env.app,
        tenant_id=TENANT_ID,
        branch_id=BRANCH_ID,
        node_id=node_id,
        readonly=True,
    ) as db:
        model = LocalReadModel(
            SyncEdgeRepository(db), readiness_guard=readiness.require_verified
        )
        catalog = await model.catalog(
            tenant_id=TENANT_ID, branch_id=BRANCH_ID, query=None, page=1, page_size=20
        )
        suppliers = await model.suppliers(
            tenant_id=TENANT_ID, branch_id=BRANCH_ID, query=None, page=1, page_size=20
        )
        inventory = await model.inventory(
            tenant_id=TENANT_ID,
            branch_id=BRANCH_ID,
            can_view_costs=False,
            show_empty=False,
            page=1,
            page_size=20,
        )
    require(catalog.total == 3 and suppliers.total == 0 and inventory.total == 5, 72)
    print(
        json.dumps(
            {
                "phase": "read",
                "status": "ok",
                "catalog": catalog.total,
                "suppliers": suppliers.total,
                "inventory": inventory.total,
                "signed_preverify": True,
            }
        )
    )


async def _node_id(env: Connections, kid: UUID) -> UUID:
    async with session(env.support, support=True) as db:
        value = await db.scalar(
            text("SELECT id FROM sync_node WHERE credential_kid=:kid"), {"kid": kid}
        )
    require(isinstance(value, UUID), 73)
    return value


def health(expected: int, expected_edge: bool | None) -> None:
    try:
        with urlopen("http://127.0.0.1:8000/healthz", timeout=5) as response:
            actual = response.status
            payload = json.load(response)
    except HTTPError as error:
        actual = error.code
        payload = json.load(error)
    require(actual == expected, 74)
    if expected_edge is not None:
        require(payload.get("edge_snapshot") is expected_edge, 75)
    print(
        json.dumps(
            {"phase": "health", "status": actual, "edge_snapshot": payload["edge_snapshot"]}
        )
    )


async def run(phase: str) -> None:
    env = connections()
    try:
        if phase == "seed":
            await seed(env)
        else:
            await read(env)
    finally:
        await env.app.dispose()
        await env.installer.dispose()
        await env.support.dispose()
        get_settings.cache_clear()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("seed", "read", "health"))
    parser.add_argument("--expected", type=int, choices=(200, 503))
    parser.add_argument("--expected-edge", choices=("true", "false", "any"))
    args = parser.parse_args()
    if args.phase == "health":
        require(args.expected is not None and args.expected_edge is not None, 76)
        health(
            args.expected,
            None if args.expected_edge == "any" else args.expected_edge == "true",
        )
    else:
        asyncio.run(run(args.phase))
