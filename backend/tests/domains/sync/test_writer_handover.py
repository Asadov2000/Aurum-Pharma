"""Writer handover lifecycle and fail-closed branch ownership rules."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.errors import BusinessRuleError, ConflictError
from app.core.time import utc_now
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.sync.activation_bootstrap import (
    ActivationSnapshotScope,
    verify_activation_bootstrap,
)
from app.domains.sync.activation_components import (
    REQUIRED_COMPONENTS,
    build_component_descriptor,
    component_manifest_hash,
    full_snapshot_hash,
)
from app.domains.sync.credentials import parse_edge_credential
from app.domains.sync.integrity import canonical_json_hash
from app.domains.sync.models import (
    SyncNode,
    SyncStream,
    SyncWriterEpoch,
    SyncWriterReadiness,
)
from app.domains.sync.repository import SyncCloudRepository
from app.domains.sync.schemas import (
    SyncActivationBootstrapChunkMetadata,
    SyncActivationBootstrapRead,
    SyncNodeCreate,
    SyncNodeCredentialRead,
    SyncShadowReportRequest,
    SyncWriterActivationRead,
    SyncWriterPrepareRequest,
    SyncWriterReadinessRequest,
    SyncWriterTransitionRequest,
)
from app.domains.sync.service import SyncAdminService, SyncCloudService


@dataclass(frozen=True)
class _CommittedHandoverScaffold:
    tenant_id: UUID
    branch_id: UUID
    register_id: UUID
    node: SyncNodeCredentialRead
    stream: SyncStream


async def _set_support_session(session: AsyncSession) -> None:
    await session.execute(text("SELECT set_config('app.support_session', 'true', true)"))


@pytest_asyncio.fixture
async def committed_handover_scaffold(
    db_engine: AsyncEngine,
) -> AsyncIterator[_CommittedHandoverScaffold]:
    tenant_id: UUID | None = None
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        async with session.begin():
            foundation = FoundationService(FoundationRepository(session))
            tenant = await foundation.create_tenant(
                payload={
                    "name": f"Writer handover {uuid4().hex[:8]}",
                    "contact_email": f"handover-{uuid4().hex[:8]}@aurum.tj",
                }
            )
            tenant_id = tenant.id
            branch = await foundation.create_branch(
                tenant_id=tenant.id,
                fields={"name": "Committed handover branch"},
            )
            register = await foundation.create_register(
                tenant_id=tenant.id,
                fields={"branch_id": branch.id, "name": "Register 1"},
            )
            repo = SyncCloudRepository(session)
            node = await SyncAdminService(repo).create_node(
                SyncNodeCreate(
                    tenant_id=tenant.id,
                    branch_id=branch.id,
                    display_name="Committed Edge writer",
                )
            )
            stream = await repo.get_stream(tenant_id=tenant.id, branch_id=branch.id)
            assert stream is not None
            report = await SyncCloudService(repo).report(
                edge_node_id=node.id,
                tenant_id=node.tenant_id,
                branch_id=node.branch_id,
                shadow_start_sequence=node.shadow_start_sequence,
                shadow_start_checksum=node.shadow_start_checksum,
                shadow_start_projection_checksum=node.shadow_start_projection_checksum,
                payload=SyncShadowReportRequest(
                    report_id=uuid4(),
                    origin_node_id=stream.writer_node_id,
                    writer_epoch=stream.writer_epoch,
                    last_sequence=stream.last_sequence,
                    source_checksum=stream.current_checksum,
                    projection_checksum=stream.current_projection_checksum,
                ),
            )
            assert report.status == "matched"

    try:
        yield _CommittedHandoverScaffold(
            tenant_id=tenant.id,
            branch_id=branch.id,
            register_id=register.id,
            node=node,
            stream=stream,
        )
    finally:
        if tenant_id is not None:
            async with AsyncSession(db_engine) as session:
                async with session.begin():
                    await _set_support_session(session)
                    for table in (
                        "sync_writer_readiness",
                        "sync_activation_bootstrap_chunk",
                        "sync_activation_bootstrap_component",
                        "sync_activation_foundation",
                        "sync_activation_bootstrap",
                        "sync_writer_activation",
                        "sync_shadow_report",
                        "sync_cursor",
                        "sync_inbox",
                        "sync_outbox",
                        "register_receipt_counter",
                    ):
                        await session.execute(
                            text(f"DELETE FROM public.{table} WHERE tenant_id = :tenant_id"),
                            {"tenant_id": tenant_id},
                        )
                    await session.execute(
                        text(
                            "DELETE FROM public.sync_node WHERE tenant_id = :tenant_id "
                            "AND node_kind = 'edge'"
                        ),
                        {"tenant_id": tenant_id},
                    )
                    await session.execute(
                        text("DELETE FROM public.sync_stream WHERE tenant_id = :tenant_id"),
                        {"tenant_id": tenant_id},
                    )
                    await session.execute(
                        text(
                            "DELETE FROM public.sync_writer_epoch " "WHERE tenant_id = :tenant_id"
                        ),
                        {"tenant_id": tenant_id},
                    )
                    await session.execute(
                        text("DELETE FROM public.sync_node WHERE tenant_id = :tenant_id"),
                        {"tenant_id": tenant_id},
                    )
                    await session.execute(
                        text("DELETE FROM public.register WHERE tenant_id = :tenant_id"),
                        {"tenant_id": tenant_id},
                    )
                    await session.execute(
                        text("DELETE FROM public.branch WHERE tenant_id = :tenant_id"),
                        {"tenant_id": tenant_id},
                    )
                    await session.execute(
                        text("DELETE FROM public.tenant WHERE id = :tenant_id"),
                        {"tenant_id": tenant_id},
                    )


def _committed_prepare_request(
    scaffold: _CommittedHandoverScaffold,
    *,
    activation_id: UUID,
) -> SyncWriterPrepareRequest:
    return SyncWriterPrepareRequest(
        activation_id=activation_id,
        tenant_id=scaffold.tenant_id,
        branch_id=scaffold.branch_id,
        edge_node_id=scaffold.node.id,
        register_id=scaffold.register_id,
        expected_writer_epoch=scaffold.stream.writer_epoch,
        expected_sequence=scaffold.stream.last_sequence,
        expected_source_checksum=scaffold.stream.current_checksum,
        expected_projection_checksum=scaffold.stream.current_projection_checksum,
    )


async def _prepare_handover(
    session: AsyncSession,
    scaffold,  # type: ignore[no-untyped-def]
) -> tuple[
    SyncAdminService,
    SyncNodeCredentialRead,
    SyncStream,
    SyncWriterActivationRead,
    SyncWriterPrepareRequest,
]:
    await _set_support_session(session)
    repo = SyncCloudRepository(session)
    admin = SyncAdminService(repo)
    node = await admin.create_node(
        SyncNodeCreate(
            tenant_id=scaffold["tenant"].id,
            branch_id=scaffold["branch"].id,
            display_name="Edge handover test",
        )
    )
    stream = await repo.get_stream(
        tenant_id=node.tenant_id,
        branch_id=node.branch_id,
    )
    assert stream is not None
    report = await SyncCloudService(repo).report(
        edge_node_id=node.id,
        tenant_id=node.tenant_id,
        branch_id=node.branch_id,
        shadow_start_sequence=node.shadow_start_sequence,
        shadow_start_checksum=node.shadow_start_checksum,
        shadow_start_projection_checksum=node.shadow_start_projection_checksum,
        payload=SyncShadowReportRequest(
            report_id=uuid4(),
            origin_node_id=stream.writer_node_id,
            writer_epoch=stream.writer_epoch,
            last_sequence=stream.last_sequence,
            source_checksum=stream.current_checksum,
            projection_checksum=stream.current_projection_checksum,
        ),
    )
    assert report.status == "matched"

    request = SyncWriterPrepareRequest(
        activation_id=uuid4(),
        tenant_id=node.tenant_id,
        branch_id=node.branch_id,
        edge_node_id=node.id,
        register_id=scaffold["register"].id,
        expected_writer_epoch=stream.writer_epoch,
        expected_sequence=stream.last_sequence,
        expected_source_checksum=stream.current_checksum,
        expected_projection_checksum=stream.current_projection_checksum,
    )
    epoch = await admin.prepare_writer(request)
    return admin, node, stream, epoch, request


def _readiness_request(epoch: SyncWriterActivationRead) -> SyncWriterReadinessRequest:
    return SyncWriterReadinessRequest(
        activation_id=epoch.activation_id,
        writer_epoch=epoch.writer_epoch,
        previous_sequence=epoch.previous_terminal_sequence,
        previous_source_checksum=epoch.previous_terminal_source_checksum,
        previous_projection_checksum=epoch.previous_terminal_projection_checksum,
        bootstrap_snapshot_hash=epoch.bootstrap_snapshot_hash,
        activation_manifest_hash=epoch.activation_manifest_hash,
        receipt_baseline_seq=epoch.receipt_baseline_seq,
    )


async def _insert_readiness(
    session: AsyncSession,
    *,
    node: SyncNodeCredentialRead,
    epoch: SyncWriterActivationRead,
) -> None:
    payload = _readiness_request(epoch)
    assert epoch.allowed_register_id is not None
    session.add(
        SyncWriterReadiness(
            activation_id=epoch.activation_id,
            tenant_id=epoch.tenant_id,
            branch_id=epoch.branch_id,
            edge_node_id=node.id,
            register_id=epoch.allowed_register_id,
            writer_epoch=epoch.writer_epoch,
            previous_sequence=payload.previous_sequence,
            previous_source_checksum=payload.previous_source_checksum,
            previous_projection_checksum=payload.previous_projection_checksum,
            bootstrap_snapshot_hash=payload.bootstrap_snapshot_hash,
            activation_manifest_hash=payload.activation_manifest_hash,
            receipt_baseline_seq=payload.receipt_baseline_seq,
            request_hash=canonical_json_hash(payload.model_dump(mode="json")),
        )
    )
    await session.flush()
    await session.execute(
        text(
            "UPDATE public.sync_writer_activation "
            "SET state = 'ready', ready_at = now() "
            "WHERE activation_id = :activation_id"
        ),
        {"activation_id": epoch.activation_id},
    )


async def test_prepare_and_cancel_are_idempotent_and_unfreeze_branch(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    admin, node, stream, prepared, request = await _prepare_handover(db_session, scaffold)

    replay = await admin.prepare_writer(request)
    assert replay.activation_id == prepared.activation_id
    assert replay.state == "prepared"
    assert prepared.writer_epoch == stream.writer_epoch + 1
    assert prepared.previous_terminal_sequence == stream.last_sequence
    assert prepared.allowed_register_id == scaffold["register"].id
    assert (
        await db_session.scalar(
            select(SyncWriterEpoch).where(SyncWriterEpoch.activation_id == prepared.activation_id)
        )
        is None
    )

    conflicting = request.model_copy(update={"expected_sequence": request.expected_sequence + 1})
    with pytest.raises(ConflictError):
        async with db_session.begin_nested():
            await admin.prepare_writer(conflicting)

    with pytest.raises(DBAPIError) as frozen_error:
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "SELECT public.assert_current_branch_writer("
                    ":tenant_id, :branch_id, NULL, false)"
                ),
                {"tenant_id": prepared.tenant_id, "branch_id": prepared.branch_id},
            )
    assert getattr(frozen_error.value.orig, "sqlstate", None) == "55000"

    transition = SyncWriterTransitionRequest(
        tenant_id=prepared.tenant_id,
        branch_id=prepared.branch_id,
        activation_manifest_hash=prepared.activation_manifest_hash,
    )
    cancelled = await admin.cancel_writer(
        activation_id=prepared.activation_id,
        payload=transition,
    )
    replayed_cancel = await admin.cancel_writer(
        activation_id=prepared.activation_id,
        payload=transition,
    )
    assert cancelled.state == "aborted"
    assert cancelled.activated_at is None
    assert cancelled.aborted_at is not None
    assert replayed_cancel.aborted_at == cancelled.aborted_at

    edge_node = await db_session.scalar(
        select(SyncNode).where(SyncNode.id == node.id).execution_options(populate_existing=True)
    )
    assert edge_node is not None
    assert edge_node.mode == "shadow_readonly"
    assert edge_node.register_id is None
    await db_session.execute(
        text("SELECT public.assert_current_branch_writer(" ":tenant_id, :branch_id, NULL, false)"),
        {"tenant_id": prepared.tenant_id, "branch_id": prepared.branch_id},
    )


async def test_activation_is_atomic_idempotent_and_fences_cloud(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    admin, node, stream, prepared, _request = await _prepare_handover(db_session, scaffold)
    transition = SyncWriterTransitionRequest(
        tenant_id=prepared.tenant_id,
        branch_id=prepared.branch_id,
        activation_manifest_hash=prepared.activation_manifest_hash,
    )

    with pytest.raises(BusinessRuleError, match="activation is disabled"):
        await admin.activate_writer(
            activation_id=prepared.activation_id,
            payload=transition,
        )

    settings = get_settings()
    previous_enabled = settings.EDGE_WRITER_ACTIVATION_ENABLED
    settings.EDGE_WRITER_ACTIVATION_ENABLED = True
    try:
        with pytest.raises(ConflictError):
            async with db_session.begin_nested():
                await admin.activate_writer(
                    activation_id=prepared.activation_id,
                    payload=transition,
                )

        await _insert_readiness(db_session, node=node, epoch=prepared)
        activated = await admin.activate_writer(
            activation_id=prepared.activation_id,
            payload=transition,
        )
        replay = await admin.activate_writer(
            activation_id=prepared.activation_id,
            payload=transition,
        )
    finally:
        settings.EDGE_WRITER_ACTIVATION_ENABLED = previous_enabled

    assert activated.state == "active"
    assert activated.activated_at is not None
    assert replay.activation_id == activated.activation_id
    assert replay.activated_at == activated.activated_at

    current_stream = await db_session.scalar(
        select(SyncStream)
        .where(SyncStream.id == stream.id)
        .execution_options(populate_existing=True)
    )
    assert current_stream is not None
    assert current_stream.writer_node_id == node.id
    assert current_stream.writer_epoch == prepared.writer_epoch
    assert current_stream.last_sequence == 0
    assert current_stream.current_checksum == prepared.root_source_checksum
    assert current_stream.current_projection_checksum == prepared.root_projection_checksum

    predecessor = await db_session.scalar(
        select(SyncWriterEpoch)
        .where(
            SyncWriterEpoch.tenant_id == prepared.tenant_id,
            SyncWriterEpoch.branch_id == prepared.branch_id,
            SyncWriterEpoch.writer_epoch == prepared.previous_writer_epoch,
        )
        .execution_options(populate_existing=True)
    )
    assert predecessor is not None
    assert predecessor.state == "fenced"
    assert predecessor.fenced_at is not None

    edge_node = await db_session.scalar(
        select(SyncNode).where(SyncNode.id == node.id).execution_options(populate_existing=True)
    )
    assert edge_node is not None
    assert edge_node.mode == "edge_writer"
    assert edge_node.register_id == scaffold["register"].id

    with pytest.raises(DBAPIError) as cloud_error:
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "SELECT public.assert_current_branch_writer("
                    ":tenant_id, :branch_id, :register_id, false)"
                ),
                {
                    "tenant_id": prepared.tenant_id,
                    "branch_id": prepared.branch_id,
                    "register_id": scaffold["register"].id,
                },
            )
    assert getattr(cloud_error.value.orig, "sqlstate", None) == "55000"

    await db_session.execute(
        text("SELECT set_config('app.edge_node_id', :edge_node_id, true)"),
        {"edge_node_id": str(node.id)},
    )
    await db_session.execute(
        text(
            "SELECT public.assert_current_branch_writer("
            ":tenant_id, :branch_id, :register_id, true)"
        ),
        {
            "tenant_id": prepared.tenant_id,
            "branch_id": prepared.branch_id,
            "register_id": scaffold["register"].id,
        },
    )


async def test_ready_handover_can_be_cancelled_and_retried_at_same_epoch(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    admin, node, _stream, prepared, request = await _prepare_handover(db_session, scaffold)
    await _insert_readiness(db_session, node=node, epoch=prepared)
    transition = SyncWriterTransitionRequest(
        tenant_id=prepared.tenant_id,
        branch_id=prepared.branch_id,
        activation_manifest_hash=prepared.activation_manifest_hash,
    )

    cancelled = await admin.cancel_writer(
        activation_id=prepared.activation_id,
        payload=transition,
    )
    assert cancelled.state == "aborted"
    assert cancelled.ready_at is not None

    retry_request = request.model_copy(update={"activation_id": uuid4()})
    retried = await admin.prepare_writer(retry_request)
    assert retried.activation_id != prepared.activation_id
    assert retried.writer_epoch == prepared.writer_epoch

    await admin.cancel_writer(
        activation_id=retried.activation_id,
        payload=SyncWriterTransitionRequest(
            tenant_id=retried.tenant_id,
            branch_id=retried.branch_id,
            activation_manifest_hash=retried.activation_manifest_hash,
        ),
    )


async def test_pending_handover_rejects_stale_or_competing_preparation(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    admin, _node, _stream, prepared, request = await _prepare_handover(db_session, scaffold)

    with pytest.raises(ConflictError):
        async with db_session.begin_nested():
            await admin.prepare_writer(request.model_copy(update={"activation_id": uuid4()}))

    with pytest.raises(ConflictError):
        async with db_session.begin_nested():
            await admin.prepare_writer(
                request.model_copy(
                    update={
                        "activation_id": uuid4(),
                        "expected_sequence": request.expected_sequence + 1,
                    }
                )
            )

    with pytest.raises(BusinessRuleError, match="cannot be revoked"):
        await admin.revoke_node(prepared.writer_node_id)

    await admin.cancel_writer(
        activation_id=prepared.activation_id,
        payload=SyncWriterTransitionRequest(
            tenant_id=prepared.tenant_id,
            branch_id=prepared.branch_id,
            activation_manifest_hash=prepared.activation_manifest_hash,
        ),
    )
    revoked = await admin.revoke_node(prepared.writer_node_id)
    assert revoked.status == "revoked"


async def test_writer_handover_ledgers_reject_direct_mutation(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    _admin, node, stream, prepared, _request = await _prepare_handover(db_session, scaffold)

    with pytest.raises(DBAPIError) as activation_error:
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "UPDATE public.sync_writer_activation "
                    "SET bootstrap_snapshot_hash = :hash "
                    "WHERE activation_id = :activation_id"
                ),
                {"activation_id": prepared.activation_id, "hash": "b" * 64},
            )
    assert getattr(activation_error.value.orig, "sqlstate", None) == "55000"

    with pytest.raises(DBAPIError) as bootstrap_error:
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "UPDATE public.sync_activation_bootstrap "
                    "SET foundation_hash = :hash "
                    "WHERE activation_id = :activation_id"
                ),
                {"activation_id": prepared.activation_id, "hash": "b" * 64},
            )
    assert getattr(bootstrap_error.value.orig, "sqlstate", None) == "55000"

    with pytest.raises(DBAPIError) as foundation_error:
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "UPDATE public.sync_activation_foundation "
                    "SET payload_hash = :hash "
                    "WHERE activation_id = :activation_id"
                ),
                {"activation_id": prepared.activation_id, "hash": "b" * 64},
            )
    assert getattr(foundation_error.value.orig, "sqlstate", None) == "55000"

    await _insert_readiness(db_session, node=node, epoch=prepared)
    with pytest.raises(DBAPIError) as readiness_error:
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "UPDATE public.sync_writer_readiness "
                    "SET request_hash = :hash "
                    "WHERE activation_id = :activation_id"
                ),
                {"activation_id": prepared.activation_id, "hash": "c" * 64},
            )
    assert getattr(readiness_error.value.orig, "sqlstate", None) == "55000"

    with pytest.raises(DBAPIError) as epoch_error:
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "UPDATE public.sync_writer_epoch "
                    "SET activation_manifest_hash = :hash "
                    "WHERE tenant_id = :tenant_id "
                    "AND branch_id = :branch_id "
                    "AND writer_epoch = :writer_epoch"
                ),
                {
                    "tenant_id": prepared.tenant_id,
                    "branch_id": prepared.branch_id,
                    "writer_epoch": stream.writer_epoch,
                    "hash": "d" * 64,
                },
            )
    assert getattr(epoch_error.value.orig, "sqlstate", None) == "55000"


async def test_full_component_ledger_is_exact_hash_bound_and_immutable(
    db_engine: AsyncEngine,
    committed_handover_scaffold: _CommittedHandoverScaffold,
) -> None:
    scaffold = committed_handover_scaffold
    activation_id = uuid4()
    foundation_digest = canonical_json_hash({})
    component_chunks = {
        component: SyncActivationBootstrapChunkMetadata(
            component=component,
            chunk_index=0,
            item_count=0,
            payload_hash=canonical_json_hash(
                {"component": component, "schema_version": 1, "items": []}
            ),
        )
        for component in REQUIRED_COMPONENTS
    }
    descriptors = [
        build_component_descriptor(component=component, chunks=[component_chunks[component]])
        for component in REQUIRED_COMPONENTS
    ]
    component_root = component_manifest_hash(descriptors)

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        async with session.begin():
            await _set_support_session(session)
            await session.execute(
                text(
                    "INSERT INTO public.register_receipt_counter ("
                    "tenant_id, branch_id, register_id, writer_epoch, last_receipt_seq"
                    ") VALUES (:tenant_id,:branch_id,:register_id,:writer_epoch,0) "
                    "ON CONFLICT (tenant_id, register_id) DO NOTHING"
                ),
                {
                    "tenant_id": scaffold.tenant_id,
                    "branch_id": scaffold.branch_id,
                    "register_id": scaffold.register_id,
                    "writer_epoch": scaffold.stream.writer_epoch,
                },
            )
            receipt_baseline = await session.scalar(
                text(
                    "SELECT last_receipt_seq FROM public.register_receipt_counter "
                    "WHERE tenant_id = :tenant_id AND register_id = :register_id"
                ),
                {"tenant_id": scaffold.tenant_id, "register_id": scaffold.register_id},
            )
            assert receipt_baseline is not None
            scope = ActivationSnapshotScope(
                activation_id=activation_id,
                tenant_id=scaffold.tenant_id,
                branch_id=scaffold.branch_id,
                edge_node_id=scaffold.node.id,
                register_id=scaffold.register_id,
                writer_epoch=scaffold.stream.writer_epoch + 1,
                previous_writer_epoch=scaffold.stream.writer_epoch,
                previous_terminal_sequence=scaffold.stream.last_sequence,
                previous_terminal_source_checksum=scaffold.stream.current_checksum,
                previous_terminal_projection_checksum=scaffold.stream.current_projection_checksum,
                receipt_baseline_seq=receipt_baseline,
            )
            bootstrap_digest = full_snapshot_hash(
                scope=scope,
                foundation_digest=foundation_digest,
                component_manifest_digest=component_root,
            )
            await session.execute(
                text(
                    "SELECT public.prepare_edge_writer_handover("
                    ":activation_id,:tenant_id,:branch_id,:edge_node_id,:register_id,"
                    ":expected_epoch,:expected_sequence,:source_checksum,"
                    ":projection_checksum,:snapshot_hash,:request_hash)"
                ),
                {
                    "activation_id": activation_id,
                    "tenant_id": scaffold.tenant_id,
                    "branch_id": scaffold.branch_id,
                    "edge_node_id": scaffold.node.id,
                    "register_id": scaffold.register_id,
                    "expected_epoch": scaffold.stream.writer_epoch,
                    "expected_sequence": scaffold.stream.last_sequence,
                    "source_checksum": scaffold.stream.current_checksum,
                    "projection_checksum": scaffold.stream.current_projection_checksum,
                    "snapshot_hash": bootstrap_digest,
                    "request_hash": canonical_json_hash({"activation_id": str(activation_id)}),
                },
            )
            activation = await SyncCloudRepository(session).get_writer_activation(activation_id)
            assert activation is not None
            scope_params = {
                "activation_id": activation_id,
                "tenant_id": scaffold.tenant_id,
                "branch_id": scaffold.branch_id,
                "edge_node_id": scaffold.node.id,
                "register_id": scaffold.register_id,
                "writer_epoch": activation.writer_epoch,
            }
            await session.execute(
                text(
                    "INSERT INTO public.sync_activation_bootstrap ("
                    "activation_id,tenant_id,branch_id,edge_node_id,register_id,writer_epoch,"
                    "capability,profile,readiness_eligible,foundation_hash,"
                    "component_manifest_hash,snapshot_hash,activation_manifest_hash"
                    ") VALUES ("
                    ":activation_id,:tenant_id,:branch_id,:edge_node_id,:register_id,"
                    ":writer_epoch,'cash_sale_v1','cash_sale_v1_full_v1',true,"
                    ":foundation_hash,:component_root,:snapshot_hash,:activation_manifest_hash)"
                ),
                {
                    **scope_params,
                    "foundation_hash": foundation_digest,
                    "component_root": component_root,
                    "snapshot_hash": bootstrap_digest,
                    "activation_manifest_hash": activation.activation_manifest_hash,
                },
            )
            await session.execute(
                text(
                    "INSERT INTO public.sync_activation_foundation ("
                    "activation_id,tenant_id,branch_id,edge_node_id,register_id,writer_epoch,"
                    "schema_version,payload,payload_hash"
                    ") VALUES ("
                    ":activation_id,:tenant_id,:branch_id,:edge_node_id,:register_id,"
                    ":writer_epoch,1,'{}'::jsonb,:foundation_hash)"
                ),
                {**scope_params, "foundation_hash": foundation_digest},
            )

            for index, descriptor in enumerate(descriptors):
                chunk = component_chunks[descriptor.component]
                await session.execute(
                    text(
                        "INSERT INTO public.sync_activation_bootstrap_component ("
                        "activation_id,component,tenant_id,branch_id,edge_node_id,register_id,"
                        "writer_epoch,schema_version,item_count,chunk_count,component_hash"
                        ") VALUES ("
                        ":activation_id,:component,:tenant_id,:branch_id,:edge_node_id,"
                        ":register_id,:writer_epoch,1,:item_count,:chunk_count,:component_hash)"
                    ),
                    {
                        **scope_params,
                        "component": descriptor.component,
                        "item_count": descriptor.item_count,
                        "chunk_count": descriptor.chunk_count,
                        "component_hash": descriptor.component_hash,
                    },
                )
                payload = {"component": descriptor.component, "schema_version": 1, "items": []}
                await session.execute(
                    text(
                        "INSERT INTO public.sync_activation_bootstrap_chunk ("
                        "activation_id,component,chunk_index,tenant_id,branch_id,edge_node_id,"
                        "register_id,writer_epoch,schema_version,item_count,payload,payload_hash"
                        ") VALUES ("
                        ":activation_id,:component,0,:tenant_id,:branch_id,:edge_node_id,"
                        ":register_id,:writer_epoch,1,:item_count,CAST(:payload AS JSONB),"
                        ":payload_hash)"
                    ),
                    {
                        **scope_params,
                        "component": descriptor.component,
                        "item_count": chunk.item_count,
                        "payload": json.dumps(payload, separators=(",", ":")),
                        "payload_hash": chunk.payload_hash,
                    },
                )
                complete = await session.scalar(
                    text("SELECT public.is_cash_sale_v1_bootstrap_complete(:activation_id)"),
                    {"activation_id": activation_id},
                )
                assert complete is (index == len(descriptors) - 1)

            with pytest.raises(DBAPIError) as mutation_error:
                async with session.begin_nested():
                    await session.execute(
                        text(
                            "UPDATE public.sync_activation_bootstrap_chunk "
                            "SET payload_hash = repeat('f',64) "
                            "WHERE activation_id = :activation_id AND component = 'catalog'"
                        ),
                        {"activation_id": activation_id},
                    )
            assert getattr(mutation_error.value.orig, "sqlstate", None) == "55000"


async def test_partial_full_component_ledger_rolls_back_at_commit(
    db_engine: AsyncEngine,
    committed_handover_scaffold: _CommittedHandoverScaffold,
) -> None:
    scaffold = committed_handover_scaffold
    activation_id = uuid4()
    foundation_digest = canonical_json_hash({})
    descriptors = [
        build_component_descriptor(
            component=component,
            chunks=[
                SyncActivationBootstrapChunkMetadata(
                    component=component,
                    chunk_index=0,
                    item_count=0,
                    payload_hash=canonical_json_hash(
                        {"component": component, "schema_version": 1, "items": []}
                    ),
                )
            ],
        )
        for component in REQUIRED_COMPONENTS
    ]
    component_root = component_manifest_hash(descriptors)
    scope = ActivationSnapshotScope(
        activation_id=activation_id,
        tenant_id=scaffold.tenant_id,
        branch_id=scaffold.branch_id,
        edge_node_id=scaffold.node.id,
        register_id=scaffold.register_id,
        writer_epoch=scaffold.stream.writer_epoch + 1,
        previous_writer_epoch=scaffold.stream.writer_epoch,
        previous_terminal_sequence=scaffold.stream.last_sequence,
        previous_terminal_source_checksum=scaffold.stream.current_checksum,
        previous_terminal_projection_checksum=scaffold.stream.current_projection_checksum,
        receipt_baseline_seq=0,
    )
    bootstrap_digest = full_snapshot_hash(
        scope=scope,
        foundation_digest=foundation_digest,
        component_manifest_digest=component_root,
    )
    params = {
        "activation_id": activation_id,
        "tenant_id": scaffold.tenant_id,
        "branch_id": scaffold.branch_id,
        "writer_epoch": scope.writer_epoch,
        "edge_node_id": scaffold.node.id,
        "register_id": scaffold.register_id,
        "previous_epoch": scope.previous_writer_epoch,
        "previous_sequence": scope.previous_terminal_sequence,
        "source_checksum": scope.previous_terminal_source_checksum,
        "projection_checksum": scope.previous_terminal_projection_checksum,
        "snapshot_hash": bootstrap_digest,
        "foundation_hash": foundation_digest,
        "component_root": component_root,
        "activation_manifest_hash": "e" * 64,
    }

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        with pytest.raises(DBAPIError) as commit_error:
            async with session.begin():
                await _set_support_session(session)
                await session.execute(
                    text(
                        "INSERT INTO public.sync_writer_activation ("
                        "activation_id,tenant_id,branch_id,writer_epoch,writer_node_id,"
                        "allowed_register_id,capability,state,root_source_checksum,"
                        "root_projection_checksum,current_source_checksum,"
                        "current_projection_checksum,previous_writer_epoch,"
                        "previous_terminal_sequence,previous_terminal_source_checksum,"
                        "previous_terminal_projection_checksum,bootstrap_snapshot_hash,"
                        "activation_manifest_hash,receipt_baseline_seq,prepare_request_hash,"
                        "prepared_at,aborted_at"
                        ") VALUES ("
                        ":activation_id,:tenant_id,:branch_id,:writer_epoch,:edge_node_id,"
                        ":register_id,'cash_sale_v1','aborted',repeat('1',64),repeat('2',64),"
                        "repeat('1',64),repeat('2',64),:previous_epoch,:previous_sequence,"
                        ":source_checksum,:projection_checksum,:snapshot_hash,"
                        ":activation_manifest_hash,0,repeat('3',64),now(),now())"
                    ),
                    params,
                )
                await session.execute(
                    text(
                        "INSERT INTO public.sync_activation_bootstrap ("
                        "activation_id,tenant_id,branch_id,edge_node_id,register_id,writer_epoch,"
                        "capability,profile,readiness_eligible,foundation_hash,"
                        "component_manifest_hash,snapshot_hash,activation_manifest_hash"
                        ") VALUES ("
                        ":activation_id,:tenant_id,:branch_id,:edge_node_id,:register_id,"
                        ":writer_epoch,'cash_sale_v1','cash_sale_v1_full_v1',true,"
                        ":foundation_hash,:component_root,:snapshot_hash,"
                        ":activation_manifest_hash)"
                    ),
                    params,
                )
                await session.execute(
                    text(
                        "INSERT INTO public.sync_activation_foundation ("
                        "activation_id,tenant_id,branch_id,edge_node_id,register_id,writer_epoch,"
                        "schema_version,payload,payload_hash"
                        ") VALUES ("
                        ":activation_id,:tenant_id,:branch_id,:edge_node_id,:register_id,"
                        ":writer_epoch,1,'{}'::jsonb,:foundation_hash)"
                    ),
                    params,
                )
        assert getattr(commit_error.value.orig, "sqlstate", None) == "55000"

    async with AsyncSession(db_engine) as session:
        assert (
            await session.scalar(
                text(
                    "SELECT count(*) FROM public.sync_writer_activation "
                    "WHERE activation_id = :activation_id"
                ),
                {"activation_id": activation_id},
            )
            == 0
        )
        assert (
            await session.scalar(
                text(
                    "SELECT count(*) FROM public.sync_activation_bootstrap "
                    "WHERE activation_id = :activation_id"
                ),
                {"activation_id": activation_id},
            )
            == 0
        )


async def test_runtime_role_reads_signed_foundation_but_cannot_report_readiness(
    db_engine: AsyncEngine,
    committed_handover_scaffold: _CommittedHandoverScaffold,
) -> None:
    scaffold = committed_handover_scaffold
    request = _committed_prepare_request(scaffold, activation_id=uuid4())
    async with AsyncSession(db_engine, expire_on_commit=False) as support_session:
        async with support_session.begin():
            await _set_support_session(support_session)
            prepared = await SyncAdminService(SyncCloudRepository(support_session)).prepare_writer(
                request
            )

    payload = _readiness_request(prepared)
    app_engine = create_async_engine(
        str(get_settings().DATABASE_URL_APP),
        poolclass=NullPool,
    )
    try:
        async with AsyncSession(app_engine, expire_on_commit=False) as app_session:
            async with app_session.begin():
                await app_session.execute(
                    text(
                        "SELECT "
                        "set_config('app.tenant_id', :tenant_id, true), "
                        "set_config('app.branch_id', :branch_id, true), "
                        "set_config('app.edge_node_id', :edge_node_id, true)"
                    ),
                    {
                        "tenant_id": str(scaffold.tenant_id),
                        "branch_id": str(scaffold.branch_id),
                        "edge_node_id": str(scaffold.node.id),
                    },
                )
                repo = SyncCloudRepository(app_session)
                service = SyncCloudService(repo)
                credential = parse_edge_credential(scaffold.node.credential)
                signed = await service.activation_foundation_bootstrap(
                    activation_id=prepared.activation_id,
                    edge_node_id=scaffold.node.id,
                    tenant_id=scaffold.tenant_id,
                    branch_id=scaffold.branch_id,
                    credential_kid=scaffold.node.credential_kid,
                    credential_digest=credential.digest,
                    credential_expires_at=scaffold.node.credential_expires_at,
                )
                manifest = verify_activation_bootstrap(
                    signed,
                    credential=scaffold.node.credential,
                    now=utc_now(),
                )
                assert manifest.activation_id == prepared.activation_id
                assert manifest.snapshot_hash == prepared.bootstrap_snapshot_hash
                assert manifest.profile == "foundation_shadow_v1"
                assert manifest.readiness_eligible is False
                foundation_payload = signed.foundation.model_dump(mode="json", by_alias=True)
                assert "contact_email" not in foundation_payload["tenant"]
                assert "contact_phone" not in foundation_payload["tenant"]
                assert "printer_config" not in foundation_payload["register"]

                with pytest.raises(BusinessRuleError, match="readiness is disabled"):
                    await service.record_writer_readiness(
                        edge_node_id=scaffold.node.id,
                        tenant_id=scaffold.tenant_id,
                        branch_id=scaffold.branch_id,
                        payload=payload,
                    )

                settings = get_settings()
                previous_enabled = settings.EDGE_WRITER_READINESS_ENABLED
                settings.EDGE_WRITER_READINESS_ENABLED = True
                try:
                    with pytest.raises(BusinessRuleError, match="Full Edge"):
                        await service.record_writer_readiness(
                            edge_node_id=scaffold.node.id,
                            tenant_id=scaffold.tenant_id,
                            branch_id=scaffold.branch_id,
                            payload=payload,
                        )
                finally:
                    settings.EDGE_WRITER_READINESS_ENABLED = previous_enabled

                activation = await repo.get_writer_activation(prepared.activation_id)
                assert activation is not None
                assert activation.state == "prepared"
                assert await repo.get_writer_readiness(prepared.activation_id) is None

                await app_session.execute(
                    text("SELECT set_config('app.edge_node_id', :edge_node_id, true)"),
                    {"edge_node_id": str(uuid4())},
                )
                assert await repo.get_writer_activation(prepared.activation_id) is None
                assert await repo.get_activation_bootstrap(prepared.activation_id) is None
                assert await repo.get_activation_foundation(prepared.activation_id) is None
    finally:
        await app_engine.dispose()


async def test_authenticated_edge_downloads_activation_foundation_endpoint(
    client: AsyncClient,
    db_engine: AsyncEngine,
    committed_handover_scaffold: _CommittedHandoverScaffold,
) -> None:
    from app.core.db import app_engine as request_app_engine

    scaffold = committed_handover_scaffold
    async with AsyncSession(db_engine, expire_on_commit=False) as support_session:
        async with support_session.begin():
            await _set_support_session(support_session)
            prepared = await SyncAdminService(SyncCloudRepository(support_session)).prepare_writer(
                _committed_prepare_request(scaffold, activation_id=uuid4())
            )

    settings = get_settings()
    previous_enabled = settings.EDGE_SYNC_ENABLED
    await request_app_engine.dispose()
    settings.EDGE_SYNC_ENABLED = True
    try:
        response = await client.get(
            f"/api/v1/sync/handover/{prepared.activation_id}/bootstrap/foundation",
            headers={
                "Authorization": f"AurumEdge {scaffold.node.credential}",
                "X-Aurum-Timestamp": str(int(time.time())),
                "X-Aurum-Nonce": str(uuid4()),
            },
        )
    finally:
        settings.EDGE_SYNC_ENABLED = previous_enabled
        await request_app_engine.dispose()

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "register" in response.json()["foundation"]
    signed = SyncActivationBootstrapRead.model_validate(response.json())
    manifest = verify_activation_bootstrap(
        signed,
        credential=scaffold.node.credential,
        now=utc_now(),
    )
    assert manifest.activation_id == prepared.activation_id
    assert manifest.edge_node_id == scaffold.node.id


async def test_concurrent_preparations_allow_only_one_pending_activation(
    db_engine: AsyncEngine,
    committed_handover_scaffold: _CommittedHandoverScaffold,
) -> None:
    scaffold = committed_handover_scaffold

    async def prepare_once(activation_id: UUID) -> UUID | None:
        request = _committed_prepare_request(scaffold, activation_id=activation_id)
        try:
            async with AsyncSession(db_engine, expire_on_commit=False) as session:
                async with session.begin():
                    await _set_support_session(session)
                    prepared = await SyncAdminService(SyncCloudRepository(session)).prepare_writer(
                        request
                    )
                    return prepared.activation_id
        except ConflictError:
            return None

    first_id = uuid4()
    second_id = uuid4()
    results = await asyncio.gather(prepare_once(first_id), prepare_once(second_id))
    assert sum(result is not None for result in results) == 1
    assert {result for result in results if result is not None} <= {first_id, second_id}

    async with AsyncSession(db_engine) as session:
        pending_count = await session.scalar(
            select(text("count(*)"))
            .select_from(text("public.sync_writer_activation"))
            .where(text("tenant_id = :tenant_id AND state IN ('prepared', 'ready')")),
            {"tenant_id": scaffold.tenant_id},
        )
    assert pending_count == 1
