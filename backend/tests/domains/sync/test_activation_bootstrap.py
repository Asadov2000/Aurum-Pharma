"""Cryptographic and scope checks for activation foundation bootstrap."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from app.domains.sync.activation_bootstrap import (
    ActivationBootstrapValidationError,
    ActivationSnapshotScope,
    build_activation_bootstrap,
    foundation_hash,
    snapshot_hash,
    verify_activation_bootstrap,
)
from app.domains.sync.credentials import EdgeCredential, issue_edge_credential
from app.domains.sync.schemas import (
    SyncActivationBootstrapRead,
    SyncActivationFoundationSnapshot,
)

ZERO_CHECKSUM = "0" * 64


def _foundation(now: datetime) -> SyncActivationFoundationSnapshot:
    tenant_id = uuid4()
    branch_id = uuid4()
    return SyncActivationFoundationSnapshot.model_validate(
        {
            "tenant": {
                "id": tenant_id,
                "name": "Аптека",
                "legal_name": "ООО Аптека",
                "inn_or_tin": "123456789",
                "registration_number": "REG-1",
                "legal_address": "Душанбе",
                "logo_url": None,
                "status": "active",
                "drug_catalog_mode": "autonomous",
                "suspended_at": None,
                "archived_at": None,
                "updated_at": now,
            },
            "settings": {
                "tenant_id": tenant_id,
                "expiry_thresholds": {"yellow": 6, "orange": 3, "red": 1},
                "expired_sale_mode": "strict",
                "refund_reason_mode": "optional",
                "session_admin_minutes": 480,
                "session_pos_minutes": 480,
                "pin_mode_enabled": False,
                "pos_payment_methods": ["cash", "qr"],
                "pos_mixed_payment_enabled": False,
                "draft_sale_lifetime_min": 30,
                "report_timezone": "Asia/Dushanbe",
                "prescription_warning_text": "Требуется рецепт",
                "updated_at": now,
            },
            "branch": {
                "id": branch_id,
                "tenant_id": tenant_id,
                "name": "Филиал 1",
                "address": "Душанбе",
                "branch_type": "pharmacy",
                "license_number": "LIC-1",
                "license_expires_at": date(2030, 1, 1),
                "working_hours": {"monday": "08:00-22:00"},
                "receipt_header": {"title": "Аптека"},
                "is_active": True,
                "updated_at": now,
            },
            "register": {
                "id": uuid4(),
                "tenant_id": tenant_id,
                "branch_id": branch_id,
                "name": "Касса 1",
                "printer_type": "thermal_80",
                "is_active": True,
                "updated_at": now,
            },
        }
    )


def _signed_bootstrap(
    now: datetime,
) -> tuple[EdgeCredential, SyncActivationBootstrapRead]:
    foundation = _foundation(now)
    credential = issue_edge_credential()
    scope = ActivationSnapshotScope(
        activation_id=uuid4(),
        tenant_id=foundation.tenant.id,
        branch_id=foundation.branch.id,
        edge_node_id=uuid4(),
        register_id=foundation.register_snapshot.id,
        writer_epoch=2,
        previous_writer_epoch=1,
        previous_terminal_sequence=0,
        previous_terminal_source_checksum=ZERO_CHECKSUM,
        previous_terminal_projection_checksum=ZERO_CHECKSUM,
        receipt_baseline_seq=7,
    )
    foundation_digest = foundation_hash(foundation)
    signed = build_activation_bootstrap(
        scope=scope,
        foundation=foundation,
        stored_foundation_hash=foundation_digest,
        stored_snapshot_hash=snapshot_hash(
            scope=scope,
            foundation_digest=foundation_digest,
        ),
        activation_manifest_hash="a" * 64,
        credential_kid=credential.kid,
        credential_digest=credential.digest,
        prepared_at=now,
        credential_expires_at=now + timedelta(hours=1),
        ttl_seconds=600,
        now=now,
    )
    return credential, signed


def test_activation_bootstrap_round_trip() -> None:
    now = datetime(2026, 7, 15, 10, tzinfo=UTC)
    credential, signed = _signed_bootstrap(now)

    manifest = verify_activation_bootstrap(signed, credential=credential.token, now=now)

    assert manifest.profile == "foundation_shadow_v1"
    assert manifest.readiness_eligible is False
    assert manifest.register_id == signed.foundation.register_snapshot.id
    assert manifest.expires_at == now + timedelta(minutes=10)
    assert signed.foundation.settings.pos_payment_methods == ["cash", "qr"]
    assert signed.foundation.settings.pos_mixed_payment_enabled is False


def test_activation_bootstrap_rejects_tampered_foundation() -> None:
    now = datetime(2026, 7, 15, 10, tzinfo=UTC)
    credential, signed = _signed_bootstrap(now)
    tampered_tenant = signed.foundation.tenant.model_copy(update={"name": "Подмена"})
    tampered = signed.model_copy(
        update={"foundation": signed.foundation.model_copy(update={"tenant": tampered_tenant})}
    )

    with pytest.raises(ActivationBootstrapValidationError, match="Foundation snapshot hash"):
        verify_activation_bootstrap(tampered, credential=credential.token, now=now)


def test_activation_bootstrap_rejects_expired_manifest() -> None:
    now = datetime(2026, 7, 15, 10, tzinfo=UTC)
    credential, signed = _signed_bootstrap(now)

    with pytest.raises(ActivationBootstrapValidationError, match="expired"):
        verify_activation_bootstrap(
            signed,
            credential=credential.token,
            now=now + timedelta(minutes=10),
        )
