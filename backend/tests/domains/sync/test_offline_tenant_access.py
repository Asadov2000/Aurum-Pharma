"""Offline candidates remain bounded by trusted current tenant access policy."""

from datetime import UTC, datetime, timedelta, timezone
from typing import cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domains.sync.offline_tenant_access import (
    CloudTenantAccessPolicyV1,
    permitted_offline_commands,
)
from app.domains.sync.schemas import OfflinePosCommand

TENANT_ID = UUID("00000001-0000-4000-8000-000000000001")
NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)
COMMANDS: tuple[OfflinePosCommand, ...] = (
    "operation.result.read",
    "receipt.print",
    "receipt.reprint",
    "sale.cash.complete",
    "shift.close",
    "shift.open",
)
READS = COMMANDS[:3]


def _policy(**updates: object) -> CloudTenantAccessPolicyV1:
    values: dict[str, object] = {
        "tenant_id": TENANT_ID,
        "version": 1,
        "tenant_status": "active",
        "mode": "full",
        "blocked_modules": (),
        "expires_at": None,
    }
    values.update(updates)
    return CloudTenantAccessPolicyV1.model_validate(values)


@pytest.mark.parametrize("status", ["active", "trial", "grace_period"])
def test_full_access_preserves_only_candidate_commands(status: str) -> None:
    policy = _policy(tenant_status=status)
    for commands in (COMMANDS, READS, ("receipt.reprint",), ()):
        assert (
            permitted_offline_commands(policy, tenant_id=TENANT_ID, commands=commands, now=NOW)
            == commands
        )


@pytest.mark.parametrize(
    ("mode", "modules", "expected"),
    [
        ("readonly", (), READS),
        ("full", ("pos",), READS),
        ("full", ("inventory",), (*READS, "shift.close", "shift.open")),
        ("full", ("inventory", "pos"), READS),
        ("full", ("catalog", "incoming", "settings", "staff", "suppliers"), COMMANDS),
    ],
)
def test_restrictions_remove_only_dependent_mutations(
    mode: str, modules: tuple[str, ...], expected: tuple[OfflinePosCommand, ...]
) -> None:
    policy = _policy(mode=mode, blocked_modules=modules)
    assert (
        permitted_offline_commands(policy, tenant_id=TENANT_ID, commands=COMMANDS, now=NOW)
        == expected
    )
    assert permitted_offline_commands(policy, tenant_id=TENANT_ID, commands=READS, now=NOW) == READS


def test_filter_can_remove_every_candidate_without_adding_reads() -> None:
    assert (
        permitted_offline_commands(
            _policy(mode="readonly"),
            tenant_id=TENANT_ID,
            commands=("sale.cash.complete",),
            now=NOW,
        )
        == ()
    )


@pytest.mark.parametrize("offset", [timedelta(microseconds=-1), timedelta(0), timedelta(days=1)])
def test_expiry_boundary_uses_supplied_time_only(offset: timedelta) -> None:
    policy = _policy(mode="readonly", blocked_modules=("inventory", "pos"), expires_at=NOW)
    expected = READS if offset < timedelta(0) else COMMANDS
    assert (
        permitted_offline_commands(policy, tenant_id=TENANT_ID, commands=COMMANDS, now=NOW + offset)
        == expected
    )


@pytest.mark.parametrize("status", ["setup", "readonly", "suspended", "archived"])
@pytest.mark.parametrize("expires_at", [None, NOW - timedelta(days=1)])
def test_legacy_lifecycle_denial_survives_policy_expiry(
    status: str, expires_at: datetime | None
) -> None:
    with pytest.raises(ValueError, match="lifecycle"):
        permitted_offline_commands(
            _policy(tenant_status=status, expires_at=expires_at),
            tenant_id=TENANT_ID,
            commands=READS,
            now=NOW,
        )


@pytest.mark.parametrize(
    "commands",
    [
        ("sale.card.complete",),
        ("receipt.print", "receipt.print"),
        ("shift.open", "receipt.print"),
        (True,),
        (b"receipt.print",),
        ["receipt.print"],
        "receipt.print",
        None,
    ],
)
def test_command_candidates_fail_closed(commands: object) -> None:
    with pytest.raises(ValueError):
        permitted_offline_commands(
            _policy(),
            tenant_id=TENANT_ID,
            commands=cast(tuple[OfflinePosCommand, ...], commands),
            now=NOW,
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"version": 0},
        {"version": 2**63},
        {"version": True},
        {"version": "1"},
        {"tenant_id": str(TENANT_ID)},
        {"tenant_id": UUID(int=0)},
        {"tenant_status": "unknown"},
        {"mode": "trial"},
        {"blocked_modules": ("unknown",)},
        {"blocked_modules": ("pos", "inventory")},
        {"blocked_modules": ("pos", "pos")},
        {"blocked_modules": ["pos"]},
        {"expires_at": NOW.replace(tzinfo=None)},
        {"expires_at": NOW.astimezone(timezone(timedelta(hours=3)))},
        {"expires_at": NOW.isoformat()},
        {"reason": "Do not include administrative notes"},
        {"max_users": 10},
    ],
)
def test_projection_rejects_invalid_fields_and_copied_corruption(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _policy(**updates)
    corrupted = _policy().model_copy(update=updates)
    with pytest.raises(ValueError):
        permitted_offline_commands(corrupted, tenant_id=TENANT_ID, commands=COMMANDS, now=NOW)


def test_projection_is_frozen_and_requires_every_field() -> None:
    policy = _policy(version=2**63 - 1)
    with pytest.raises(ValidationError, match="frozen"):
        policy.mode = "readonly"
    for key in policy.model_fields:
        values = policy.model_dump()
        del values[key]
        with pytest.raises(ValidationError, match="required"):
            CloudTenantAccessPolicyV1.model_validate(values)


@pytest.mark.parametrize("now", [NOW.replace(tzinfo=None), NOW.isoformat(), None, 0])
def test_evaluation_requires_typed_utc_time(now: object) -> None:
    with pytest.raises(ValueError, match="UTC"):
        permitted_offline_commands(
            _policy(), tenant_id=TENANT_ID, commands=COMMANDS, now=cast(datetime, now)
        )


def test_evaluation_rejects_non_utc_time_and_cross_tenant_policy() -> None:
    with pytest.raises(ValueError, match="UTC"):
        permitted_offline_commands(
            _policy(),
            tenant_id=TENANT_ID,
            commands=COMMANDS,
            now=NOW.astimezone(timezone(timedelta(hours=3))),
        )
    with pytest.raises(ValueError, match="scope"):
        permitted_offline_commands(
            _policy(),
            tenant_id=UUID("00000002-0000-4000-8000-000000000001"),
            commands=COMMANDS,
            now=NOW,
        )
    with pytest.raises(ValueError):
        permitted_offline_commands(
            _policy(), tenant_id=cast(UUID, str(TENANT_ID)), commands=COMMANDS, now=NOW
        )
