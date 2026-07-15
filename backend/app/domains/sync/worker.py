"""Standalone development worker for Cloud-to-Edge shadow replication."""

from __future__ import annotations

import argparse
import asyncio
import random
import time
from typing import cast
from uuid import uuid4

import httpx
import structlog
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import AppSessionLocal
from app.core.logging import configure_logging
from app.core.time import utc_now
from app.domains.sync.bootstrap import (
    BootstrapValidationError,
    chunk_as_pull,
    verify_chunk,
    verify_manifest,
)
from app.domains.sync.repository import SyncEdgeRepository
from app.domains.sync.schemas import (
    EdgeApplyResult,
    SyncBootstrapChunkRead,
    SyncBootstrapManifestRead,
    SyncPullResponse,
    SyncShadowReportRead,
    SyncShadowReportRequest,
)
from app.domains.sync.service import SyncEdgeApplyService

logger = structlog.get_logger("sync.worker")
_LOCAL_HTTP_HOSTS = {"localhost", "127.0.0.1", "::1", "backend", "host.docker.internal"}


def _cloud_base_url(settings: Settings) -> httpx.URL:
    if settings.EDGE_SYNC_CLOUD_URL is None:
        raise RuntimeError("EDGE_SYNC_CLOUD_URL is required for the Edge worker")
    url = httpx.URL(str(settings.EDGE_SYNC_CLOUD_URL))
    if url.username or url.password or url.query or url.fragment:
        raise RuntimeError("EDGE_SYNC_CLOUD_URL must not contain credentials, query, or fragment")
    if url.scheme != "https" and not (
        settings.ENVIRONMENT == "development"
        and url.scheme == "http"
        and url.host in _LOCAL_HTTP_HOSTS
    ):
        raise RuntimeError("Edge Cloud transport requires HTTPS outside local development")
    return url


def _request_headers(credential: str) -> dict[str, str]:
    return {
        "Authorization": f"AurumEdge {credential}",
        "X-Aurum-Timestamp": str(int(time.time())),
        "X-Aurum-Nonce": str(uuid4()),
    }


async def _set_edge_scope(session: AsyncSession, pull: SyncPullResponse) -> None:
    for name, value in (
        ("app.tenant_id", pull.tenant_id),
        ("app.branch_id", pull.branch_id),
        ("app.edge_node_id", pull.edge_node_id),
    ):
        await session.execute(
            text("SELECT set_config(:name, :value, true)"),
            {"name": name, "value": str(value)},
        )


async def _apply_pull(pull: SyncPullResponse) -> EdgeApplyResult:
    async with AppSessionLocal() as session:
        async with session.begin():
            await _set_edge_scope(session, pull)
            service = SyncEdgeApplyService(SyncEdgeRepository(session))
            result = await service.apply(pull)
            if result.status == "synced":
                result = await service.verify_projection(
                    tenant_id=pull.tenant_id,
                    branch_id=pull.branch_id,
                    origin_node_id=pull.origin_node_id,
                    writer_epoch=pull.writer_epoch,
                )
            return result


async def _pull(
    client: httpx.AsyncClient,
    *,
    credential: str,
    after_sequence: int,
    limit: int,
) -> SyncPullResponse:
    response = await client.get(
        "/api/v1/sync/pull",
        params={"after_sequence": after_sequence, "limit": limit},
        headers=_request_headers(credential),
    )
    response.raise_for_status()
    return SyncPullResponse.model_validate(cast(object, response.json()))


async def _bootstrap_manifest(
    client: httpx.AsyncClient,
    *,
    credential: str,
) -> SyncBootstrapManifestRead:
    response = await client.get(
        "/api/v1/sync/bootstrap/manifest",
        headers=_request_headers(credential),
    )
    response.raise_for_status()
    return SyncBootstrapManifestRead.model_validate(cast(object, response.json()))


async def _bootstrap_chunk(
    client: httpx.AsyncClient,
    *,
    credential: str,
    bootstrap_id: str,
    chunk_index: int,
) -> SyncBootstrapChunkRead:
    response = await client.get(
        f"/api/v1/sync/bootstrap/{bootstrap_id}/chunks/{chunk_index}",
        headers=_request_headers(credential),
    )
    response.raise_for_status()
    return SyncBootstrapChunkRead.model_validate(cast(object, response.json()))


async def _local_cursor(pull: SyncPullResponse) -> tuple[str, int, bool] | None:
    async with AppSessionLocal() as session:
        async with session.begin():
            await _set_edge_scope(session, pull)
            cursor = await SyncEdgeRepository(session).get_cursor(
                tenant_id=pull.tenant_id,
                branch_id=pull.branch_id,
                origin_node_id=pull.origin_node_id,
                writer_epoch=pull.writer_epoch,
            )
            if cursor is None:
                return None
            return cursor.status, cursor.last_sequence, cursor.last_event_id is not None


def _assert_manifest_matches_pull(
    signed: SyncBootstrapManifestRead,
    pull: SyncPullResponse,
) -> None:
    manifest = signed.manifest
    if (
        manifest.edge_node_id != pull.edge_node_id
        or manifest.tenant_id != pull.tenant_id
        or manifest.branch_id != pull.branch_id
        or manifest.origin_node_id != pull.origin_node_id
        or manifest.writer_epoch != pull.writer_epoch
        or manifest.checkpoint_sequence != pull.effective_after_sequence
        or manifest.source_checksum != pull.after_source_checksum
        or manifest.projection_checksum != pull.after_projection_checksum
    ):
        raise BootstrapValidationError("Bootstrap does not match the Cloud pull checkpoint")


async def _bootstrap_if_required(
    client: httpx.AsyncClient,
    *,
    credential: str,
    pull: SyncPullResponse,
) -> None:
    checkpoint = pull.effective_after_sequence
    if checkpoint == 0:
        return
    cursor = await _local_cursor(pull)
    if cursor is not None:
        status, last_sequence, has_last_event = cursor
        if status != "synced":
            raise BootstrapValidationError("Local Edge cursor is not healthy")
        if last_sequence > checkpoint:
            return
        if last_sequence == checkpoint:
            if not has_last_event:
                raise BootstrapValidationError("Local Edge cursor has no bootstrap proof")
            return
        current_sequence = last_sequence
    else:
        current_sequence = 0

    signed = await _bootstrap_manifest(client, credential=credential)
    manifest = verify_manifest(signed, credential=credential, now=utc_now())
    _assert_manifest_matches_pull(signed, pull)
    for descriptor in manifest.chunks:
        if descriptor.last_sequence <= current_sequence:
            continue
        if descriptor.first_sequence != current_sequence + 1:
            raise BootstrapValidationError("Local Edge bootstrap cursor has a gap")
        chunk = await _bootstrap_chunk(
            client,
            credential=credential,
            bootstrap_id=str(manifest.bootstrap_id),
            chunk_index=descriptor.index,
        )
        verify_chunk(manifest, chunk)
        result = await _apply_pull(chunk_as_pull(manifest=manifest, chunk=chunk))
        if (
            result.status != "synced"
            or result.last_sequence != descriptor.last_sequence
            or result.source_checksum != descriptor.source_checksum
            or result.projection_checksum != descriptor.projection_checksum
        ):
            raise BootstrapValidationError("Local Edge rejected a bootstrap chunk")
        current_sequence = result.last_sequence
    if current_sequence != checkpoint:
        raise BootstrapValidationError("Local Edge bootstrap is incomplete")


async def _report(
    client: httpx.AsyncClient,
    *,
    credential: str,
    pull: SyncPullResponse,
    result: EdgeApplyResult,
) -> SyncShadowReportRead:
    payload = SyncShadowReportRequest(
        report_id=uuid4(),
        origin_node_id=pull.origin_node_id,
        writer_epoch=pull.writer_epoch,
        last_sequence=result.last_sequence,
        source_checksum=result.source_checksum,
        projection_checksum=result.projection_checksum,
    )
    response = await client.post(
        "/api/v1/sync/report",
        headers=_request_headers(credential),
        json=payload.model_dump(mode="json"),
    )
    response.raise_for_status()
    return SyncShadowReportRead.model_validate(cast(object, response.json()))


async def run_cycle(client: httpx.AsyncClient, settings: Settings, credential: str) -> None:
    pull = await _pull(
        client,
        credential=credential,
        after_sequence=0,
        limit=settings.EDGE_SYNC_BATCH_SIZE,
    )
    await _bootstrap_if_required(client, credential=credential, pull=pull)
    final_pull: SyncPullResponse | None = None
    final_result: EdgeApplyResult | None = None
    while True:
        result = await _apply_pull(pull)
        if result.status != "synced":
            logger.error(
                "edge_sync_halted",
                edge_node_id=str(pull.edge_node_id),
                branch_id=str(pull.branch_id),
                sequence=result.last_sequence,
                status=result.status,
            )
            return
        final_pull = pull
        final_result = result
        if not pull.has_more:
            break
        pull = await _pull(
            client,
            credential=credential,
            after_sequence=result.last_sequence,
            limit=settings.EDGE_SYNC_BATCH_SIZE,
        )

    if final_pull is None or final_result is None:
        return
    report = await _report(
        client,
        credential=credential,
        pull=final_pull,
        result=final_result,
    )
    if report.status != "matched":
        logger.error(
            "edge_shadow_report_mismatch",
            edge_node_id=str(final_pull.edge_node_id),
            branch_id=str(final_pull.branch_id),
            sequence=report.last_sequence,
        )


async def run(*, once: bool) -> None:
    settings = get_settings()
    if settings.EDGE_SYNC_CREDENTIAL is None:
        raise RuntimeError("EDGE_SYNC_CREDENTIAL is required for the Edge worker")
    credential = settings.EDGE_SYNC_CREDENTIAL.get_secret_value()
    base_url = _cloud_base_url(settings)
    timeout = httpx.Timeout(15.0, connect=5.0)
    backoff = 1.0
    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=timeout,
        follow_redirects=False,
    ) as client:
        while True:
            try:
                await run_cycle(client, settings, credential)
                backoff = 1.0
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "edge_sync_http_error",
                    status_code=exc.response.status_code,
                )
            except httpx.RequestError as exc:
                logger.warning("edge_sync_transport_error", error_type=type(exc).__name__)
            except SQLAlchemyError as exc:
                logger.warning("edge_sync_database_error", error_type=type(exc).__name__)
            except (ValueError, RuntimeError) as exc:
                logger.error("edge_sync_protocol_error", error_type=type(exc).__name__)
            if once:
                return
            delay = settings.EDGE_SYNC_POLL_SECONDS
            if backoff > 1:
                delay = max(delay, int(backoff + random.uniform(0, backoff / 4)))
            await asyncio.sleep(delay)
            backoff = min(backoff * 2, 60.0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Aurum Edge shadow sync worker")
    parser.add_argument("--once", action="store_true", help="Run one pull/apply/report cycle")
    args = parser.parse_args()
    configure_logging()
    asyncio.run(run(once=bool(args.once)))


if __name__ == "__main__":
    main()
