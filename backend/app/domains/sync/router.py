"""Support enrollment and authenticated Cloud pull/report endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_db,
    require_recent_platform_capability,
)
from app.domains.sync.auth import EdgeRequestContext, get_edge_context
from app.domains.sync.bootstrap import BootstrapScope
from app.domains.sync.repository import SyncCloudRepository
from app.domains.sync.schemas import (
    SyncActivationBootstrapRead,
    SyncBootstrapChunkRead,
    SyncBootstrapManifestRead,
    SyncCredentialRotate,
    SyncMonitoringHealth,
    SyncMonitoringMode,
    SyncMonitoringRead,
    SyncNodeCreate,
    SyncNodeCredentialRead,
    SyncNodeRead,
    SyncPullResponse,
    SyncShadowReportRead,
    SyncShadowReportRequest,
    SyncWriterActivationRead,
    SyncWriterEpochRead,
    SyncWriterPrepareRequest,
    SyncWriterReadinessRead,
    SyncWriterReadinessRequest,
    SyncWriterTransitionRequest,
)
from app.domains.sync.service import SyncAdminService, SyncCloudService

admin_router = APIRouter(
    prefix="/api/v1/admin/sync",
    tags=["admin-sync"],
)
router = APIRouter(prefix="/api/v1/sync", tags=["edge-sync"])


def _prevent_credential_caching(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _admin_service(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> SyncAdminService:
    return SyncAdminService(SyncCloudRepository(db))


def _bootstrap_scope(context: EdgeRequestContext) -> BootstrapScope:
    principal = context.principal
    return BootstrapScope(
        edge_node_id=principal.node_id,
        tenant_id=principal.tenant_id,
        branch_id=principal.branch_id,
        credential_kid=principal.credential_kid,
        credential_digest=principal.credential_digest,
        credential_issued_at=principal.credential_issued_at,
        credential_expires_at=principal.credential_expires_at,
        origin_node_id=principal.shadow_start_origin_node_id,
        writer_epoch=principal.shadow_start_writer_epoch,
        root_source_checksum=principal.shadow_root_source_checksum,
        root_projection_checksum=principal.shadow_root_projection_checksum,
        checkpoint_sequence=principal.shadow_start_sequence,
        source_checksum=principal.shadow_start_checksum,
        projection_checksum=principal.shadow_start_projection_checksum,
    )


@admin_router.post(
    "/nodes",
    response_model=SyncNodeCredentialRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_recent_platform_capability("platform.sync.manage"))],
)
async def create_node(
    payload: SyncNodeCreate,
    response: Response,
    service: Annotated[SyncAdminService, Depends(_admin_service)],
) -> SyncNodeCredentialRead:
    _prevent_credential_caching(response)
    return await service.create_node(payload)


@admin_router.get(
    "/nodes",
    response_model=list[SyncNodeRead],
    deprecated=True,
    dependencies=[Depends(require_recent_platform_capability("platform.sync.manage"))],
)
async def list_nodes(
    response: Response,
    service: Annotated[SyncAdminService, Depends(_admin_service)],
    tenant_id: Annotated[UUID | None, Query()] = None,
) -> list[SyncNodeRead]:
    _prevent_credential_caching(response)
    return await service.list_nodes(tenant_id=tenant_id)


@admin_router.get(
    "/overview",
    response_model=SyncMonitoringRead,
    dependencies=[Depends(require_recent_platform_capability("platform.sync.view"))],
)
async def monitoring_overview(
    response: Response,
    service: Annotated[SyncAdminService, Depends(_admin_service)],
    tenant_id: Annotated[UUID | None, Query()] = None,
    health: Annotated[SyncMonitoringHealth | None, Query()] = None,
    mode: Annotated[SyncMonitoringMode | None, Query()] = None,
    query: Annotated[str | None, Query(alias="q", max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SyncMonitoringRead:
    _prevent_credential_caching(response)
    return await service.monitoring_overview(
        tenant_id=tenant_id,
        health=health,
        mode=mode,
        query=query,
        limit=limit,
        offset=offset,
    )


@admin_router.post(
    "/nodes/{node_id}/credential",
    response_model=SyncNodeCredentialRead,
    dependencies=[Depends(require_recent_platform_capability("platform.sync.manage"))],
)
async def rotate_node_credential(
    node_id: UUID,
    payload: SyncCredentialRotate,
    response: Response,
    service: Annotated[SyncAdminService, Depends(_admin_service)],
) -> SyncNodeCredentialRead:
    _prevent_credential_caching(response)
    return await service.rotate_credential(
        node_id=node_id,
        valid_days=payload.credential_valid_days,
    )


@admin_router.delete(
    "/nodes/{node_id}",
    response_model=SyncNodeRead,
    dependencies=[Depends(require_recent_platform_capability("platform.sync.manage"))],
)
async def revoke_node(
    node_id: UUID,
    service: Annotated[SyncAdminService, Depends(_admin_service)],
) -> SyncNodeRead:
    return await service.revoke_node(node_id)


@admin_router.post(
    "/handover/prepare",
    response_model=SyncWriterActivationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_recent_platform_capability("platform.sync.manage"))],
)
async def prepare_writer_handover(
    payload: SyncWriterPrepareRequest,
    service: Annotated[SyncAdminService, Depends(_admin_service)],
) -> SyncWriterActivationRead:
    return await service.prepare_writer(payload)


@admin_router.post(
    "/handover/{activation_id}/activate",
    response_model=SyncWriterEpochRead,
    dependencies=[Depends(require_recent_platform_capability("platform.sync.manage"))],
)
async def activate_writer_handover(
    activation_id: UUID,
    payload: SyncWriterTransitionRequest,
    service: Annotated[SyncAdminService, Depends(_admin_service)],
) -> SyncWriterEpochRead:
    return await service.activate_writer(activation_id=activation_id, payload=payload)


@admin_router.post(
    "/handover/{activation_id}/cancel",
    response_model=SyncWriterActivationRead,
    dependencies=[Depends(require_recent_platform_capability("platform.sync.manage"))],
)
async def cancel_writer_handover(
    activation_id: UUID,
    payload: SyncWriterTransitionRequest,
    service: Annotated[SyncAdminService, Depends(_admin_service)],
) -> SyncWriterActivationRead:
    return await service.cancel_writer(activation_id=activation_id, payload=payload)


@router.get("/pull", response_model=SyncPullResponse)
async def pull_events(
    context: Annotated[
        EdgeRequestContext,
        Depends(get_edge_context, scope="function"),
    ],
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
) -> SyncPullResponse:
    principal = context.principal
    return await SyncCloudService(SyncCloudRepository(context.session)).pull(
        edge_node_id=principal.node_id,
        tenant_id=principal.tenant_id,
        branch_id=principal.branch_id,
        shadow_start_sequence=principal.shadow_start_sequence,
        shadow_start_checksum=principal.shadow_start_checksum,
        shadow_start_projection_checksum=principal.shadow_start_projection_checksum,
        shadow_start_origin_node_id=principal.shadow_start_origin_node_id,
        shadow_start_writer_epoch=principal.shadow_start_writer_epoch,
        after_sequence=after_sequence,
        limit=limit,
    )


@router.get("/bootstrap/manifest", response_model=SyncBootstrapManifestRead)
async def get_bootstrap_manifest(
    response: Response,
    context: Annotated[
        EdgeRequestContext,
        Depends(get_edge_context, scope="function"),
    ],
) -> SyncBootstrapManifestRead:
    _prevent_credential_caching(response)
    return await SyncCloudService(SyncCloudRepository(context.session)).bootstrap_manifest(
        scope=_bootstrap_scope(context)
    )


@router.get(
    "/bootstrap/{bootstrap_id}/chunks/{chunk_index}",
    response_model=SyncBootstrapChunkRead,
)
async def get_bootstrap_chunk(
    bootstrap_id: UUID,
    response: Response,
    context: Annotated[
        EdgeRequestContext,
        Depends(get_edge_context, scope="function"),
    ],
    chunk_index: Annotated[int, Path(ge=0)],
) -> SyncBootstrapChunkRead:
    _prevent_credential_caching(response)
    return await SyncCloudService(SyncCloudRepository(context.session)).bootstrap_chunk(
        bootstrap_id=bootstrap_id,
        chunk_index=chunk_index,
        scope=_bootstrap_scope(context),
    )


@router.get(
    "/handover/{activation_id}/bootstrap/foundation",
    response_model=SyncActivationBootstrapRead,
)
async def get_activation_foundation_bootstrap(
    activation_id: UUID,
    response: Response,
    context: Annotated[
        EdgeRequestContext,
        Depends(get_edge_context, scope="function"),
    ],
) -> SyncActivationBootstrapRead:
    _prevent_credential_caching(response)
    principal = context.principal
    return await SyncCloudService(
        SyncCloudRepository(context.session)
    ).activation_foundation_bootstrap(
        activation_id=activation_id,
        edge_node_id=principal.node_id,
        tenant_id=principal.tenant_id,
        branch_id=principal.branch_id,
        credential_kid=principal.credential_kid,
        credential_digest=principal.credential_digest,
        credential_expires_at=principal.credential_expires_at,
    )


@router.post("/report", response_model=SyncShadowReportRead)
async def report_shadow_checkpoint(
    payload: SyncShadowReportRequest,
    context: Annotated[
        EdgeRequestContext,
        Depends(get_edge_context, scope="function"),
    ],
) -> SyncShadowReportRead:
    principal = context.principal
    return await SyncCloudService(SyncCloudRepository(context.session)).report(
        edge_node_id=principal.node_id,
        tenant_id=principal.tenant_id,
        branch_id=principal.branch_id,
        shadow_start_sequence=principal.shadow_start_sequence,
        shadow_start_checksum=principal.shadow_start_checksum,
        shadow_start_projection_checksum=principal.shadow_start_projection_checksum,
        payload=payload,
    )


@router.post(
    "/handover/readiness",
    response_model=SyncWriterReadinessRead,
    status_code=status.HTTP_201_CREATED,
)
async def record_writer_readiness(
    payload: SyncWriterReadinessRequest,
    context: Annotated[
        EdgeRequestContext,
        Depends(get_edge_context, scope="function"),
    ],
) -> SyncWriterReadinessRead:
    principal = context.principal
    return await SyncCloudService(SyncCloudRepository(context.session)).record_writer_readiness(
        edge_node_id=principal.node_id,
        tenant_id=principal.tenant_id,
        branch_id=principal.branch_id,
        payload=payload,
    )
