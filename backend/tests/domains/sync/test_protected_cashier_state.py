"""Cross-language snapshot contract; no database, TPM or runtime admission."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.domains.sync.local_credentials import (
    LocalCredentialContextV1,
    local_credential_context_digest,
)
from app.domains.sync.protected_cashier_state import (
    MAX_PROTECTED_CASHIER_STATE_BYTES,
    decode_protected_cashier_state,
    encode_protected_cashier_state,
    protected_cashier_state_digest,
)

_FIXTURE = Path(__file__).resolve().parents[4] / "docs/fixtures/protected-cashier-state-v1.json"
_DOMAIN = b"aurum:protected-cashier-state:v1\x00"


def _document() -> dict[str, object]:
    parsed: object = json.loads(_FIXTURE.read_bytes())
    assert isinstance(parsed, dict)
    return parsed


def _at(document: object, path: str) -> object:
    current = document
    for key in path.split("."):
        if isinstance(current, dict):
            current = current[key]
        else:
            assert isinstance(current, list)
            current = current[int(key)]
    return current


def _set(document: dict[str, object], path: str, value: object) -> None:
    prefix, _, key = path.rpartition(".")
    parent = document if not prefix else _at(document, prefix)
    assert isinstance(parent, dict)
    parent[key] = value


def _raw(document: dict[str, object]) -> bytes:
    return json.dumps(document, ensure_ascii=False).encode("utf-8")


def test_shared_golden_exact_digest_and_canonical_round_trip() -> None:
    raw = _FIXTURE.read_bytes()
    snapshot = decode_protected_cashier_state(raw)
    expected = _FIXTURE.with_suffix(".sha256").read_text(encoding="ascii").strip()
    assert protected_cashier_state_digest(raw).hex() == expected
    assert hashlib.sha256(_DOMAIN + raw).hexdigest() == expected
    assert encode_protected_cashier_state(snapshot) == raw
    assert snapshot.cashiers[0].guard.failed_attempts == 1
    assert snapshot.workplace_session is not None
    assert snapshot.workplace_session.confirmation is not None


def test_hash_binds_exact_bytes_including_whitespace_and_escaped_strings() -> None:
    raw = _FIXTURE.read_bytes()
    spaced = b" \n" + raw + b"\t"
    escaped = raw.replace(b"boot-fixture-1", b"boot-fixture-\\u0031")
    assert decode_protected_cashier_state(raw) == decode_protected_cashier_state(spaced)
    assert decode_protected_cashier_state(raw) == decode_protected_cashier_state(escaped)
    assert len({protected_cashier_state_digest(value) for value in (raw, spaced, escaped)}) == 3


@pytest.mark.parametrize("status", ["setup", "readonly", "suspended", "archived"])
def test_new_deny_floor_and_expired_session_are_valid_data(status: str) -> None:
    wire = _document()
    _set(wire, "policy_floor.policy.version", 15)
    _set(wire, "policy_floor.policy.tenant_status", status)
    _set(wire, "observed_time.utc_now", "2026-11-04T09:00:02Z")
    _set(wire, "cashiers.0.guard.blocked_until", "2026-11-02T09:00:00Z")
    snapshot = decode_protected_cashier_state(_raw(wire))
    assert snapshot.policy_floor.policy.version == 15
    assert snapshot.workplace_session is not None
    assert snapshot.workplace_session.expires_at < snapshot.observed_time.utc_now


def test_empty_cashiers_and_explicit_null_session_are_valid_data() -> None:
    wire = _document()
    wire["cashiers"] = []
    wire["workplace_session"] = None
    snapshot = decode_protected_cashier_state(_raw(wire))
    assert snapshot.cashiers == ()
    assert snapshot.workplace_session is None
    assert json.loads(encode_protected_cashier_state(snapshot))["workplace_session"] is None


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("schema_version", True),
        ("schema_version", 2),
        ("scope.writer_epoch", True),
        ("scope.writer_epoch", 0),
        ("scope.writer_epoch", 9_223_372_036_854_775_808),
        ("scope.capability", "sale_cash"),
        ("scope.tenant_id", "00000002000040008000000000000001"),
        ("scope.tenant_id", "00000002-0000-1000-8000-000000000001"),
        ("scope.tenant_id", "00000002-0000-4000-7000-000000000001"),
        ("checkpoint_context_sha256", "AB" * 32),
        ("checkpoint_context_sha256", "ab" * 31),
        ("observed_time.utc_now", 1_793_696_402),
        ("observed_time.utc_now", "2026-11-03"),
        ("observed_time.utc_now", "2026-11-03T09:00:02"),
        ("observed_time.utc_now", "2026-11-03T09:00:02+01:00"),
        ("observed_time.utc_now", "2026-11-03T09:00:02.0000001Z"),
        ("observed_time.utc_now", "2026-02-30T09:00:02Z"),
        ("observed_time.continuity_id", ""),
        ("observed_time.continuity_id", "x" * 129),
        ("policy_floor.delivery_sequence", 0),
        ("policy_floor.claims_hash", "wrong"),
        ("policy_floor.policy.version", 13),
        ("policy_floor.policy.mode", "unknown"),
        ("policy_floor.policy.tenant_id", "000000ff-0000-4000-8000-000000000001"),
        ("policy_floor.policy.blocked_modules", ["pos", "catalog"]),
        ("policy_floor.policy.blocked_modules", ["pos", "pos"]),
        ("cashiers.0.guard.failed_attempts", -1),
        ("cashiers.0.guard.failed_attempts", 2_147_483_648),
        ("cashiers.0.guard.attempt_sequence", -1),
        ("cashiers.0.guard.credential_revision", 4),
        ("cashiers.0.guard.context_digest", "00" * 32),
        ("cashiers.0.guard.user_id", "000000ff-0000-4000-8000-000000000001"),
        ("cashiers.0.guard.last_utc", "2026-11-03T09:00:03Z"),
        ("cashiers.0.guard.last_counter", 103),
        ("cashiers.0.guard.continuity_id", "other-boot"),
        ("cashiers.0.authorization.status", "unknown"),
        ("cashiers.0.authorization.scope.writer_epoch", 7),
        ("cashiers.0.authorization.device_binding.spki_sha256", "00" * 32),
        ("cashiers.0.authorization.allowed_commands", ["shift.open", "receipt.print"]),
        ("cashiers.0.authorization.allowed_commands", ["receipt.print", "receipt.print"]),
        ("cashiers.0.authorization.allowed_commands", ["invented.command"]),
        ("cashiers.0.authorization.reauthentication_policy.max_session_seconds", 86_401),
        ("cashiers.0.credential_context.audience", "other-audience"),
        ("cashiers.0.credential_context.local_credential.revision", 4),
        ("workplace_session.lease_id", "000000ff-0000-4000-8000-000000000001"),
        ("workplace_session.scope.user_id", "000000ff-0000-4000-8000-000000000001"),
        ("workplace_session.local_credential.revision", 4),
        ("workplace_session.reauthentication_policy.revision", 5),
        ("workplace_session.shift_context_id", "000000ff-0000-4000-8000-000000000001"),
        ("workplace_session.authenticated_at", "2026-11-03T09:00:03Z"),
        ("workplace_session.expires_at", "2026-11-03T09:00:00Z"),
        ("workplace_session.continuity_id", "other-boot"),
        ("workplace_session.monotonic_counter", 103),
        ("workplace_session.confirmation.confirmed_at", "2026-11-03T09:00:03Z"),
        ("workplace_session.confirmation.confirmed_at", "2026-11-03T08:59:59Z"),
        ("workplace_session.confirmation.monotonic_counter", 99),
        ("workplace_session.confirmation.monotonic_counter", 103),
    ],
)
def test_rejects_invalid_fields_and_cross_record_substitution(path: str, value: object) -> None:
    wire = _document()
    _set(wire, path, value)
    with pytest.raises(ValueError):
        decode_protected_cashier_state(_raw(wire))


@pytest.mark.parametrize("path", ["", "scope", "cashiers.0", "cashiers.0.guard"])
def test_rejects_unknown_fields_at_every_layer(path: str) -> None:
    wire = _document()
    _set(wire, f"{path}.pepper" if path else "pepper", "not-a-secret")
    with pytest.raises(ValueError):
        decode_protected_cashier_state(_raw(wire))


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{}\x00",
        b'{"schema_version":1,"schema_version":1}',
        b'{"nested":{"a":1,"a":2}}',
        b'{"schema_version":1.0}',
        b'{"schema_version":1e0}',
        b'{"schema_version":NaN}',
        b'{"schema_version":Infinity}',
        b'{"schema_version":-Infinity}',
        b'{"value":"\\ud800"}',
        b'{"\\udfff":1}',
        b"\xff",
        b"\xef\xbb\xbf{}",
        b"[]",
        b"null",
    ],
)
def test_rejects_ambiguous_or_malformed_json(raw: bytes) -> None:
    with pytest.raises(ValueError):
        decode_protected_cashier_state(raw)
    with pytest.raises(ValueError):
        protected_cashier_state_digest(raw)


def test_rejects_payload_and_depth_limits_before_model_validation() -> None:
    with pytest.raises(ValueError, match="size"):
        decode_protected_cashier_state(b" " * (MAX_PROTECTED_CASHIER_STATE_BYTES + 1))
    with pytest.raises(ValueError, match="nesting"):
        decode_protected_cashier_state(b"[" * 33 + b"0" + b"]" * 33)


def test_rejects_missing_null_pointer_duplicate_and_unsorted_users_and_count_limit() -> None:
    wire = _document()
    del wire["workplace_session"]
    with pytest.raises(ValueError):
        decode_protected_cashier_state(_raw(wire))
    wire = _document()
    session = _at(wire, "workplace_session")
    assert isinstance(session, dict)
    del session["confirmation"]
    with pytest.raises(ValueError, match="explicit confirmation"):
        decode_protected_cashier_state(_raw(wire))
    wire = _document()
    first = _at(wire, "cashiers.0")
    wire["cashiers"] = [first, first]
    with pytest.raises(ValueError, match="unique sorted"):
        decode_protected_cashier_state(_raw(wire))
    second: object = json.loads(json.dumps(first))
    assert isinstance(second, dict)
    user = "00000008-0000-4000-8000-000000000001"
    for path in (
        "authorization.scope.user_id",
        "credential_context.scope.user_id",
        "guard.user_id",
    ):
        _set(second, path, user)
    context = LocalCredentialContextV1.model_validate_json(
        json.dumps(_at(second, "credential_context"))
    )
    _set(second, "guard.context_digest", local_credential_context_digest(context).hex())
    wire["cashiers"] = [second, first]
    with pytest.raises(ValueError, match="unique sorted"):
        decode_protected_cashier_state(_raw(wire))
    wire["cashiers"] = [first, second]
    assert len(decode_protected_cashier_state(_raw(wire)).cashiers) == 2
    wire["cashiers"] = [first] * 257
    with pytest.raises(ValueError):
        decode_protected_cashier_state(_raw(wire))


def test_uppercase_uuid_utc_offset_microseconds_and_unicode_continuity_parity() -> None:
    wire = _document()
    # Non-canonical input remains valid; its exact bytes get their own digest.
    _set(wire, "observed_time.utc_now", "2026-11-03T09:00:02.123456+00:00")
    _set(wire, "checkpoint_context_sha256", "ef" * 32)
    for path in (
        "observed_time.continuity_id",
        "cashiers.0.guard.continuity_id",
        "workplace_session.continuity_id",
    ):
        _set(wire, path, "\U0001f48a" * 128)
    raw = _raw(wire).replace(b"0000000c-0000-4000", b"0000000C-0000-4000")
    snapshot = decode_protected_cashier_state(raw)
    assert snapshot.observed_time.utc_now.microsecond == 123456
    assert len(snapshot.observed_time.continuity_id) == 128
    assert decode_protected_cashier_state(encode_protected_cashier_state(snapshot)) == snapshot


def test_guard_sql_integer_upper_bound_and_null_session_are_preserved() -> None:
    wire = _document()
    wire["workplace_session"] = None
    _set(wire, "cashiers.0.guard.failed_attempts", 2_147_483_647)
    snapshot = decode_protected_cashier_state(_raw(wire))
    assert snapshot.cashiers[0].guard.failed_attempts == 2_147_483_647
