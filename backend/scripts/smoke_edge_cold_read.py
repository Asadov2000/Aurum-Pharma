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


async def install(env: Connections, *, revision: int, interrupt: bool = False) -> None:
    credential = parse_edge_credential(os.environ["EDGE_SYNC_CREDENTIAL"])
    if revision == 1:
        await verify_environment(env)
        node_id = await _scope(env, credential.kid, credential.digest)
    else:
        node_id = await _node_id(env, credential.kid)
    projection = _projection(3 + (revision - 1), 5)
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
            source_revision=revision,
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
        if interrupt:
            # Simulate an installer process crash after every SQL write, including
            # the active cursor, but before PostgreSQL can commit the transaction.
            os._exit(77)
    print(json.dumps({"phase": "install", "revision": revision, "status": "ok"}))


async def read(env: Connections, *, expected_revision: int) -> None:
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
        active = await SyncEdgeRepository(db).get_active_read_projection(
            tenant_id=TENANT_ID,
            branch_id=BRANCH_ID,
            credential_kid=credential.kid,
        )
        require(active is not None and active[0].source_revision == expected_revision, 77)
        revision_two_count = await db.scalar(
            text(
                "SELECT count(*) FROM sync_read_snapshot "
                "WHERE tenant_id=:tenant_id AND branch_id=:branch_id "
                "AND node_id=:node_id AND source_revision=2"
            ),
            {"tenant_id": TENANT_ID, "branch_id": BRANCH_ID, "node_id": node_id},
        )
        require(revision_two_count == expected_revision - 1, 78)
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
    require(
        catalog.total == expected_revision + 2
        and suppliers.total == 0
        and inventory.total == 5,
        72,
    )
    print(
        json.dumps(
            {
                "phase": "read",
                "status": "ok",
                "catalog": catalog.total,
                "revision": expected_revision,
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
            await install(env, revision=1)
        elif phase == "interrupt":
            await install(env, revision=2, interrupt=True)
        elif phase == "recover":
            await install(env, revision=2)
        else:
            await read(env, expected_revision=2 if phase == "read_recovered" else 1)
    finally:
        await env.app.dispose()
        await env.installer.dispose()
        await env.support.dispose()
        get_settings.cache_clear()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase", choices=("seed", "read", "interrupt", "recover", "read_recovered", "health")
    )
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
