"""Edge credential lifecycle, request replay, and rate-limit guards."""

from __future__ import annotations

import time
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError as PydanticValidationError
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from starlette.requests import Request

from app.core.config import Settings, get_settings
from app.core.db import app_engine
from app.core.errors import AuthenticationError, RateLimitError
from app.core.time import utc_now
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.pos.schemas import SaleCheckoutResult
from app.domains.sync.auth import (
    _claim_nonce,
    _parse_request_auth,
    _rate_limit,
    get_edge_context,
)
from app.domains.sync.credentials import parse_edge_credential
from app.domains.sync.integrity import (
    canonical_json_hash,
    projection_stream_checksum,
    sale_projection_hash,
    source_stream_checksum,
)
from app.domains.sync.models import SyncStream
from app.domains.sync.repository import SyncCloudRepository, SyncEdgeRepository
from app.domains.sync.schemas import (
    SyncEventEnvelope,
    SyncNodeCreate,
    SyncNodeCredentialRead,
    SyncPullResponse,
)
from app.domains.sync.service import SyncAdminService, SyncEdgeApplyService


async def _enable_edge_sync_with_fresh_app_pool(settings: Settings) -> None:
    # The request pool is process-scoped, while pytest gives async tests
    # separate event loops. Discard any pool left by an earlier loop.
    await app_engine.dispose(close=False)
    settings.EDGE_SYNC_ENABLED = True


def test_edge_node_display_name_is_normalized_and_cannot_be_blank() -> None:
    tenant_id = uuid4()
    branch_id = uuid4()

    payload = SyncNodeCreate(
        tenant_id=tenant_id,
        branch_id=branch_id,
        display_name="  Main pharmacy Edge  ",
    )
    assert payload.display_name == "Main pharmacy Edge"

    with pytest.raises(PydanticValidationError):
        SyncNodeCreate(
            tenant_id=tenant_id,
            branch_id=branch_id,
            display_name="   ",
        )


def _valid_pull(issued: SyncNodeCredentialRead, stream: SyncStream) -> SyncPullResponse:
    occurred_at = utc_now()
    event_id = uuid4()
    sale_id = uuid4()
    operation_id = uuid4()
    sale = SaleCheckoutResult(
        event_id=event_id,
        sale_id=sale_id,
        operation_id=operation_id,
        tenant_id=issued.tenant_id,
        branch_id=issued.branch_id,
        register_id=uuid4(),
        shift_id=uuid4(),
        cashier_user_id=uuid4(),
        receipt_number="000001",
        receipt_seq=1,
        created_at=occurred_at,
        completed_at=occurred_at,
        total_amount=Decimal("0.00"),
        currency="TJS",
        is_test=False,
        items=[],
        payments=[],
    )
    payload = sale.model_dump(mode="json")
    payload_digest = canonical_json_hash(payload)
    projection_digest = sale_projection_hash(payload)
    source_checksum = source_stream_checksum(
        previous_checksum=issued.shadow_start_checksum,
        event_id=event_id,
        tenant_id=issued.tenant_id,
        branch_id=issued.branch_id,
        origin_node_id=stream.writer_node_id,
        writer_epoch=stream.writer_epoch,
        sequence=1,
        operation_id=operation_id,
        aggregate_type="sale",
        aggregate_id=sale_id,
        event_type="pos.sale.completed.v1",
        schema_version=1,
        occurred_at=occurred_at,
        payload_hash=payload_digest,
    )
    projection_checksum = projection_stream_checksum(
        previous_checksum=issued.shadow_start_projection_checksum,
        origin_node_id=stream.writer_node_id,
        writer_epoch=stream.writer_epoch,
        sequence=1,
        sale_id=sale_id,
        projection_hash=projection_digest,
    )
    envelope = SyncEventEnvelope(
        event_id=event_id,
        tenant_id=issued.tenant_id,
        branch_id=issued.branch_id,
        origin_node_id=stream.writer_node_id,
        writer_epoch=stream.writer_epoch,
        sequence=1,
        operation_id=operation_id,
        aggregate_type="sale",
        aggregate_id=sale_id,
        event_type="pos.sale.completed.v1",
        schema_version=1,
        occurred_at=occurred_at,
        payload=payload,
        payload_hash=payload_digest,
        stream_checksum=source_checksum,
        projection_hash=projection_digest,
        projection_checksum=projection_checksum,
    )
    return SyncPullResponse(
        edge_node_id=issued.id,
        tenant_id=issued.tenant_id,
        branch_id=issued.branch_id,
        origin_node_id=stream.writer_node_id,
        writer_epoch=stream.writer_epoch,
        effective_after_sequence=issued.shadow_start_sequence,
        after_source_checksum=issued.shadow_start_checksum,
        after_projection_checksum=issued.shadow_start_projection_checksum,
        cloud_last_sequence=1,
        events=[envelope],
        has_more=False,
    )


async def test_edge_secret_is_one_time_and_revocation_is_immediate(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    service = SyncAdminService(SyncCloudRepository(db_session))
    issued = await service.create_node(
        SyncNodeCreate(
            tenant_id=scaffold["tenant"].id,
            branch_id=scaffold["branch"].id,
            display_name="Credential test",
        )
    )
    parsed = parse_edge_credential(issued.credential)
    stored = await SyncCloudRepository(db_session).get_edge_node(issued.id)
    assert stored is not None
    assert stored.credential_hash == parsed.digest
    assert parsed.secret not in stored.credential_hash

    result = await db_session.execute(
        text("SELECT * FROM public.authenticate_edge_node(:kid, :digest)"),
        {"kid": parsed.kid, "digest": parsed.digest},
    )
    assert result.mappings().one()["node_id"] == issued.id

    await service.revoke_node(issued.id)
    result = await db_session.execute(
        text("SELECT * FROM public.authenticate_edge_node(:kid, :digest)"),
        {"kid": parsed.kid, "digest": parsed.digest},
    )
    assert result.mappings().one_or_none() is None


async def test_expired_edge_credential_is_rejected(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    service = SyncAdminService(SyncCloudRepository(db_session))
    issued = await service.create_node(
        SyncNodeCreate(
            tenant_id=scaffold["tenant"].id,
            branch_id=scaffold["branch"].id,
            display_name="Expired credential",
        )
    )
    parsed = parse_edge_credential(issued.credential)
    node = await SyncCloudRepository(db_session).get_edge_node(issued.id)
    assert node is not None
    node.credential_expires_at = utc_now().replace(year=2000)
    await db_session.flush()

    result = await db_session.execute(
        text("SELECT * FROM public.authenticate_edge_node(:kid, :digest)"),
        {"kid": parsed.kid, "digest": parsed.digest},
    )
    assert result.mappings().one_or_none() is None


async def test_inactive_branch_pauses_edge_credential_until_restored(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    foundation = FoundationService(FoundationRepository(db_session))
    await foundation.create_branch(
        tenant_id=scaffold["tenant"].id,
        fields={"name": "Keep active"},
    )
    service = SyncAdminService(SyncCloudRepository(db_session))
    issued = await service.create_node(
        SyncNodeCreate(
            tenant_id=scaffold["tenant"].id,
            branch_id=scaffold["branch"].id,
            display_name="Paused with branch",
        )
    )
    parsed = parse_edge_credential(issued.credential)

    await foundation.soft_delete_branch(scaffold["branch"].id)
    result = await db_session.execute(
        text("SELECT * FROM public.authenticate_edge_node(:kid, :digest)"),
        {"kid": parsed.kid, "digest": parsed.digest},
    )
    assert result.mappings().one_or_none() is None

    await foundation.update_branch(scaffold["branch"].id, fields={"is_active": True})
    result = await db_session.execute(
        text("SELECT * FROM public.authenticate_edge_node(:kid, :digest)"),
        {"kid": parsed.kid, "digest": parsed.digest},
    )
    assert result.mappings().one()["node_id"] == issued.id


def test_user_bearer_token_is_not_machine_auth() -> None:
    with pytest.raises(AuthenticationError):
        _parse_request_auth(
            authorization="Bearer user-jwt",
            timestamp_header=str(int(time.time())),
            nonce_header=str(uuid4()),
        )


async def test_nonce_replay_and_fixed_window_rate_limit(redis: Redis) -> None:
    digest = uuid4().hex * 2
    nonce = uuid4()
    await _claim_nonce(redis, credential_hash=digest, nonce=nonce, ttl=60)
    with pytest.raises(AuthenticationError):
        await _claim_nonce(redis, credential_hash=digest, nonce=nonce, ttl=60)

    key = f"sync:test:rate:{uuid4()}"
    await _rate_limit(redis, key=key, limit=2)
    await _rate_limit(redis, key=key, limit=2)
    with pytest.raises(RateLimitError):
        await _rate_limit(redis, key=key, limit=2)


async def test_machine_dependency_derives_rls_scope_from_committed_credential(
    db_engine: AsyncEngine,
    redis: Redis,
) -> None:
    tenant_id = None
    issued = None
    stream = None
    settings = get_settings()
    previous_enabled = settings.EDGE_SYNC_ENABLED
    try:
        async with AsyncSession(db_engine, expire_on_commit=False) as session:
            async with session.begin():
                foundation = FoundationService(FoundationRepository(session))
                tenant = await foundation.create_tenant(
                    payload={
                        "name": f"Edge auth {uuid4().hex[:8]}",
                        "contact_email": f"edge-auth-{uuid4().hex[:8]}@aurum.tj",
                    }
                )
                tenant_id = tenant.id
                branch = await foundation.create_branch(
                    tenant_id=tenant.id,
                    fields={"name": "Machine branch"},
                )
                issued = await SyncAdminService(SyncCloudRepository(session)).create_node(
                    SyncNodeCreate(
                        tenant_id=tenant.id,
                        branch_id=branch.id,
                        display_name="Committed Edge",
                    )
                )
                stream = await SyncCloudRepository(session).get_stream(
                    tenant_id=tenant.id,
                    branch_id=branch.id,
                )

        await _enable_edge_sync_with_fresh_app_pool(settings)
        assert issued is not None
        assert stream is not None
        nonce = uuid4()
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/sync/pull",
                "headers": [],
                "query_string": b"",
                "scheme": "http",
                "server": ("testserver", 80),
                "client": ("127.0.0.1", 12345),
            }
        )
        dependency = get_edge_context(
            request,
            redis,
            authorization=f"AurumEdge {issued.credential}",
            request_timestamp_header=str(int(time.time())),
            nonce_header=str(nonce),
        )
        context = await anext(dependency)
        assert context.principal.node_id == issued.id
        assert context.principal.tenant_id == issued.tenant_id
        assert context.principal.credential_kid == parse_edge_credential(issued.credential).kid
        assert context.principal.shadow_start_origin_node_id == stream.writer_node_id
        assert context.principal.shadow_start_writer_epoch == stream.writer_epoch
        assert context.principal.shadow_root_source_checksum == "0" * 64
        assert context.principal.shadow_root_projection_checksum == "0" * 64
        db_scope = (
            await context.session.execute(
                text(
                    "SELECT current_setting('app.tenant_id'), "
                    "current_setting('app.branch_id'), current_setting('app.edge_node_id')"
                )
            )
        ).one()
        assert db_scope == (str(issued.tenant_id), str(issued.branch_id), str(issued.id))
        applied = await SyncEdgeApplyService(SyncEdgeRepository(context.session)).apply(
            _valid_pull(issued, stream)
        )
        assert applied.status == "synced"
        assert applied.last_sequence == 1
        with pytest.raises(StopAsyncIteration):
            await anext(dependency)

        replay = get_edge_context(
            request,
            redis,
            authorization=f"AurumEdge {issued.credential}",
            request_timestamp_header=str(int(time.time())),
            nonce_header=str(nonce),
        )
        with pytest.raises(AuthenticationError):
            await anext(replay)
    finally:
        settings.EDGE_SYNC_ENABLED = previous_enabled
        await app_engine.dispose()
        if tenant_id is not None:
            async with db_engine.begin() as connection:
                for table in (
                    "sync_shadow_report",
                    "sync_writer_readiness",
                    "sync_sale_projection",
                    "sync_cursor",
                    "sync_inbox",
                    "sync_outbox",
                    "register_receipt_counter",
                ):
                    await connection.execute(
                        text(f"DELETE FROM {table} WHERE tenant_id = :tenant_id"),
                        {"tenant_id": tenant_id},
                    )
                await connection.execute(
                    text(
                        "DELETE FROM sync_node WHERE tenant_id = :tenant_id "
                        "AND node_kind = 'edge'"
                    ),
                    {"tenant_id": tenant_id},
                )
                await connection.execute(
                    text("DELETE FROM sync_stream WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant_id},
                )
                await connection.execute(
                    text("DELETE FROM sync_writer_epoch WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant_id},
                )
                await connection.execute(
                    text("DELETE FROM sync_node WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant_id},
                )
                await connection.execute(
                    text("DELETE FROM tenant WHERE id = :tenant_id"),
                    {"tenant_id": tenant_id},
                )
