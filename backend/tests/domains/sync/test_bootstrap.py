"""Cryptographic and temporal guards for the Edge bootstrap contract."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from app.core.time import utc_now
from app.domains.sync.bootstrap import (
    BootstrapValidationError,
    build_manifest,
    verify_manifest,
)
from app.domains.sync.credentials import EdgeCredential, issue_edge_credential
from app.domains.sync.integrity import ZERO_CHECKSUM
from app.domains.sync.schemas import SyncBootstrapManifestRead


def _empty_manifest(
    *, issued_offset: timedelta = timedelta()
) -> tuple[EdgeCredential, SyncBootstrapManifestRead]:
    credential = issue_edge_credential()
    issued_at = utc_now() + issued_offset
    signed, chunks = build_manifest(
        edge_node_id=uuid4(),
        tenant_id=uuid4(),
        branch_id=uuid4(),
        credential_kid=credential.kid,
        credential_digest=credential.digest,
        credential_issued_at=issued_at,
        credential_expires_at=issued_at + timedelta(days=1),
        origin_node_id=uuid4(),
        writer_epoch=1,
        root_source_checksum=ZERO_CHECKSUM,
        root_projection_checksum=ZERO_CHECKSUM,
        checkpoint_sequence=0,
        source_checksum=ZERO_CHECKSUM,
        projection_checksum=ZERO_CHECKSUM,
        events=[],
        chunk_size=100,
        ttl_seconds=300,
    )
    assert chunks == []
    return credential, signed


def test_bootstrap_manifest_rejects_signature_tamper_and_wrong_credential() -> None:
    credential, signed = _empty_manifest()
    verified = verify_manifest(signed, credential=credential.token, now=utc_now())
    assert verified.checkpoint_sequence == 0

    tampered = signed.model_copy(update={"signature": "0" * 64})
    with pytest.raises(BootstrapValidationError, match="signature"):
        verify_manifest(tampered, credential=credential.token, now=utc_now())

    other = issue_edge_credential()
    with pytest.raises(BootstrapValidationError, match="credential scope"):
        verify_manifest(signed, credential=other.token, now=utc_now())


def test_bootstrap_manifest_rejects_expired_and_future_windows() -> None:
    expired_credential, expired = _empty_manifest(issued_offset=timedelta(hours=-1))
    with pytest.raises(BootstrapValidationError, match="expired"):
        verify_manifest(expired, credential=expired_credential.token, now=utc_now())

    future_credential, future = _empty_manifest(issued_offset=timedelta(minutes=6))
    with pytest.raises(BootstrapValidationError, match="future"):
        verify_manifest(future, credential=future_credential.token, now=utc_now())
