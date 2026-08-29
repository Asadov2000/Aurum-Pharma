"""Safe platform monitoring read model for Edge synchronization."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.domains.sync.integrity import ZERO_CHECKSUM
from app.domains.sync.models import SyncNode
from app.domains.sync.repository import SyncCloudRepository
from app.domains.sync.schemas import (
    SyncNodeCreate,
    SyncQuarantineIncidentRequest,
    SyncShadowReportRequest,
)
from app.domains.sync.service import (
    SyncAdminService,
    SyncCloudService,
    _quarantine_evidence_hash,
)


async def _enroll(
    session: AsyncSession,
    *,
    tenant_id,
    branch_id,
    display_name: str,
):  # type: ignore[no-untyped-def]
    return await SyncAdminService(SyncCloudRepository(session)).create_node(
        SyncNodeCreate(
            tenant_id=tenant_id,
            branch_id=branch_id,
            display_name=display_name,
        )
    )


async def _report(
    session: AsyncSession,
    node,
    *,
    source_checksum: str = ZERO_CHECKSUM,
):  # type: ignore[no-untyped-def]
    return await SyncCloudService(SyncCloudRepository(session)).report(
        edge_node_id=node.id,
        tenant_id=node.tenant_id,
        branch_id=node.branch_id,
        shadow_start_sequence=node.shadow_start_sequence,
        shadow_start_checksum=node.shadow_start_checksum,
        shadow_start_projection_checksum=node.shadow_start_projection_checksum,
        payload=SyncShadowReportRequest(
            report_id=uuid4(),
            origin_node_id=node.shadow_start_origin_node_id,
            writer_epoch=node.shadow_start_writer_epoch,
            last_sequence=node.shadow_start_sequence,
            source_checksum=source_checksum,
            projection_checksum=node.shadow_start_projection_checksum,
        ),
    )


async def test_monitoring_classifies_health_and_keeps_pagination_total(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    healthy = await _enroll(
        db_session,
        tenant_id=scaffold["tenant"].id,
        branch_id=scaffold["branch"].id,
        display_name="Healthy Edge",
    )
    expired = await _enroll(
        db_session,
        tenant_id=scaffold["tenant"].id,
        branch_id=scaffold["branch"].id,
        display_name="Expired Edge",
    )
    mismatch = await _enroll(
        db_session,
        tenant_id=scaffold["tenant"].id,
        branch_id=scaffold["branch"].id,
        display_name="Mismatch Edge",
    )
    now = utc_now()
    await db_session.execute(
        update(SyncNode)
        .where(SyncNode.id.in_([healthy.id, expired.id, mismatch.id]))
        .values(last_seen_at=now)
    )
    await db_session.execute(
        update(SyncNode)
        .where(SyncNode.id == expired.id)
        .values(credential_expires_at=now - timedelta(minutes=1))
    )
    await _report(db_session, healthy)
    await _report(db_session, mismatch, source_checksum="f" * 64)

    service = SyncAdminService(SyncCloudRepository(db_session))
    overview = await service.monitoring_overview(
        tenant_id=scaffold["tenant"].id,
        health=None,
        mode=None,
        query=None,
        limit=2,
        offset=2,
    )

    assert overview.total == 3
    assert len(overview.items) == 1
    assert overview.summary.total_nodes == 3
    assert overview.summary.healthy_nodes == 1
    assert overview.summary.critical_nodes == 2

    by_name = await service.monitoring_overview(
        tenant_id=scaffold["tenant"].id,
        health="healthy",
        mode="shadow_readonly",
        query="  Healthy   Edge ",
        limit=25,
        offset=0,
    )
    assert by_name.total == 1
    assert by_name.items[0].display_name == "Healthy Edge"
    assert by_name.items[0].contact_state == "recent"
    assert by_name.items[0].integrity_state == "verified"
    assert "credential_kid" not in by_name.model_dump(mode="json")["items"][0]


async def test_monitoring_tenant_scope_does_not_mix_nodes(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    first = await pos_scaffold()
    second = await pos_scaffold()
    await _enroll(
        db_session,
        tenant_id=first["tenant"].id,
        branch_id=first["branch"].id,
        display_name="First tenant Edge",
    )
    await _enroll(
        db_session,
        tenant_id=second["tenant"].id,
        branch_id=second["branch"].id,
        display_name="Second tenant Edge",
    )

    overview = await SyncAdminService(SyncCloudRepository(db_session)).monitoring_overview(
        tenant_id=first["tenant"].id,
        health=None,
        mode=None,
        query=None,
        limit=25,
        offset=0,
    )

    assert overview.total == 1
    assert overview.items[0].tenant_id == first["tenant"].id
    assert overview.items[0].display_name == "First tenant Edge"
    assert all(item.tenant_id != second["tenant"].id for item in overview.items)


async def test_monitoring_marks_quarantined_node_critical_without_business_payload(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    node = await _enroll(
        db_session,
        tenant_id=scaffold["tenant"].id,
        branch_id=scaffold["branch"].id,
        display_name="Quarantined Edge",
    )
    payload = SyncQuarantineIncidentRequest(
        incident_id=uuid4(),
        origin_node_id=node.shadow_start_origin_node_id,
        writer_epoch=node.shadow_start_writer_epoch,
        cursor_status="mismatch",
        reason_code="response_checkpoint_mismatch",
        last_applied_sequence=node.shadow_start_sequence,
        source_checksum=node.shadow_start_checksum,
        projection_checksum=node.shadow_start_projection_checksum,
        observed_at=utc_now(),
        evidence_hash="0" * 64,
    )
    payload = payload.model_copy(
        update={
            "evidence_hash": _quarantine_evidence_hash(
                tenant_id=node.tenant_id,
                branch_id=node.branch_id,
                edge_node_id=node.id,
                payload=payload,
            )
        }
    )
    recorded = await SyncCloudService(SyncCloudRepository(db_session)).report_quarantine_incident(
        edge_node_id=node.id,
        tenant_id=node.tenant_id,
        branch_id=node.branch_id,
        payload=payload,
    )
    assert recorded.replayed is False

    overview = await SyncAdminService(SyncCloudRepository(db_session)).monitoring_overview(
        tenant_id=node.tenant_id,
        health=None,
        mode=None,
        query="Quarantined Edge",
        limit=25,
        offset=0,
    )
    item = overview.items[0]
    assert overview.summary.quarantined_nodes == 1
    assert item.health == "critical"
    assert item.quarantine_incident_count == 1
    assert item.latest_quarantine_reason == "response_checkpoint_mismatch"
    assert item.latest_quarantine_status == "mismatch"
    assert item.latest_quarantine_sequence == node.shadow_start_sequence
    serialized = item.model_dump(mode="json")
    assert "payload" not in serialized
    assert "payments" not in serialized
