"""The Edge read benchmark must never reach a shared database."""

from __future__ import annotations

from unittest.mock import Mock
from uuid import UUID

import pytest

from scripts import benchmark_edge_read_model as benchmark

_PASSWORD = "synthetic-benchmark-only"


@pytest.fixture
def guarded_environment(monkeypatch: pytest.MonkeyPatch) -> Mock:
    monkeypatch.setenv(
        "DATABASE_URL_APP",
        "postgresql+asyncpg://aurum_app:"
        f"{_PASSWORD}@edge-benchmark-postgres:5432/aurum_edge_read_benchmark_test",
    )
    monkeypatch.setenv(
        "DATABASE_URL_SUPPORT",
        "postgresql+asyncpg://aurum_support:"
        f"{_PASSWORD}@edge-benchmark-postgres:5432/aurum_edge_read_benchmark_test",
    )
    constructor = Mock(side_effect=AssertionError("client constructed before validation"))
    monkeypatch.setattr(benchmark, "create_async_engine", constructor)
    return constructor


@pytest.mark.parametrize(
    ("variable", "role"),
    [
        ("DATABASE_URL_APP", "aurum_app"),
        ("DATABASE_URL_SUPPORT", "aurum_support"),
    ],
)
@pytest.mark.parametrize(
    ("host", "database", "role_override", "suffix"),
    [
        ("postgres", benchmark.DATABASE_NAME, None, ""),
        ("localhost", benchmark.DATABASE_NAME, None, ""),
        (benchmark.DATABASE_HOST, "aurum", None, ""),
        (benchmark.DATABASE_HOST, "aurum_test", None, ""),
        (benchmark.DATABASE_HOST, benchmark.DATABASE_NAME, "postgres", ""),
        (benchmark.DATABASE_HOST, benchmark.DATABASE_NAME, None, "?sslmode=disable"),
    ],
)
def test_nonbenchmark_database_is_refused_before_client_construction(
    monkeypatch: pytest.MonkeyPatch,
    guarded_environment: Mock,
    variable: str,
    role: str,
    host: str,
    database: str,
    role_override: str | None,
    suffix: str,
) -> None:
    monkeypatch.setenv(
        variable,
        f"postgresql+asyncpg://{role_override or role}:{_PASSWORD}@{host}:5432/{database}{suffix}",
    )

    with pytest.raises(benchmark.BenchmarkFailure) as failure:
        benchmark.connections()

    assert failure.value.reason == 10
    guarded_environment.assert_not_called()


def test_valid_destinations_construct_only_the_two_bounded_engines(
    guarded_environment: Mock,
) -> None:
    guarded_environment.side_effect = None
    guarded_environment.return_value = Mock()

    result = benchmark.connections()

    assert result.app is guarded_environment.return_value
    assert result.support is guarded_environment.return_value
    assert guarded_environment.call_count == 2


def test_percentile_uses_nearest_rank_and_summary_is_milliseconds() -> None:
    values = [float(value) for value in range(1, 21)]

    assert benchmark.percentile(values, 0.95) == 19.0
    assert benchmark.summarize(values) == {
        "samples": 20,
        "p50_ms": 10.5,
        "p95_ms": 19.0,
        "max_ms": 20.0,
    }


def test_fixture_scope_and_target_are_deterministic() -> None:
    foundation = benchmark.foundation_payload()
    tenant = foundation["tenant"]
    branch = foundation["branch"]

    assert foundation["record_type"] == "foundation"
    assert isinstance(tenant, dict) and tenant["id"] == str(benchmark.TENANT_ID)
    assert isinstance(branch, dict) and branch["id"] == str(benchmark.BRANCH_ID)
    assert benchmark.TARGET_QUERY in "Product targetterm"
    assert len(benchmark.TARGET_BARCODE) == 13
    assert benchmark.STABILIZATION_IDLE_SECONDS == 30


def test_negative_rls_scopes_change_one_coordinate_at_a_time() -> None:
    node_id = UUID("51000000-0000-0000-0000-000000000099")

    assert benchmark.negative_rls_scopes(node_id) == (
        (benchmark.FOREIGN_TENANT_ID, benchmark.BRANCH_ID, node_id),
        (benchmark.TENANT_ID, benchmark.FOREIGN_BRANCH_ID, node_id),
        (benchmark.TENANT_ID, benchmark.BRANCH_ID, benchmark.FOREIGN_NODE_ID),
    )


def test_microbenchmark_reports_references_without_acceptance_claim() -> None:
    claims = benchmark.benchmark_claims()

    assert claims["acceptance_claimed"] is False
    assert "thresholds_met" not in claims
    references = claims["reference_budgets_ms"]
    assert isinstance(references, dict)
    assert references["inventory_p95_ms"] == {
        "value": 300,
        "kind": "diagnostic_reference",
    }
