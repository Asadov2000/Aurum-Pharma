"""The write benchmark must fail before clients can reach non-disposable data."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import Mock

import pytest

from app.domains.catalog.import_limits import MAX_IMPORT_FILE_BYTES
from app.domains.catalog.import_parser import parse_import
from scripts import benchmark_catalog_write as benchmark

_DATABASE_ROLES = (
    ("DATABASE_URL_APP", "aurum_app"),
    ("DATABASE_URL_SUPPORT", "aurum_support"),
    ("DATABASE_URL_MIGRATION", "aurum_migrator"),
)
_SYNTHETIC_PASSWORD = "synthetic-benchmark-only"


@pytest.fixture
def guarded_environment(monkeypatch: pytest.MonkeyPatch) -> tuple[Mock, ...]:
    """Install inert destinations and fail on even constructing an I/O client."""
    for variable, role in _DATABASE_ROLES:
        monkeypatch.setenv(
            variable,
            f"postgresql+asyncpg://{role}:{_SYNTHETIC_PASSWORD}"
            "@benchmark-postgres:5432/aurum_catalog_benchmark_test",
        )
    values = {
        "CELERY_BROKER_URL": (f"redis://aurum_api:{_SYNTHETIC_PASSWORD}@benchmark-redis:6379/0"),
        "MINIO_ENDPOINT": "benchmark-minio:9000",
        "MINIO_BUCKET": "catalog-benchmark",
        "MINIO_ROOT_USER": "synthetic-root",
        "MINIO_ROOT_PASSWORD": _SYNTHETIC_PASSWORD,
        "MINIO_SECURE": "false",
    }
    for variable, value in values.items():
        monkeypatch.setenv(variable, value)
    clients = []
    for name in ("create_async_engine", "Minio", "PoolManager"):
        client = Mock(side_effect=AssertionError("I/O client constructed before validation"))
        monkeypatch.setattr(benchmark, name, client)
        clients.append(client)
    return tuple(clients)


@pytest.mark.parametrize(("variable", "role"), _DATABASE_ROLES)
@pytest.mark.parametrize(
    ("host", "database", "role_override"),
    [
        ("postgres", "aurum_catalog_benchmark_test", None),
        ("localhost", "aurum_catalog_benchmark_test", None),
        ("benchmark-postgres", "aurum", None),
        ("benchmark-postgres", "aurum_test", None),
        ("benchmark-postgres", "aurum_catalog_benchmark_test", "postgres"),
        ("benchmark-postgres", "aurum_catalog_benchmark_test", "aurum_catalog_worker"),
    ],
)
def test_nonbenchmark_database_refused_before_any_client(
    monkeypatch: pytest.MonkeyPatch,
    guarded_environment: tuple[Mock, ...],
    variable: str,
    role: str,
    host: str,
    database: str,
    role_override: str | None,
) -> None:
    monkeypatch.setenv(
        variable,
        f"postgresql+asyncpg://{role_override or role}:{_SYNTHETIC_PASSWORD}"
        f"@{host}:5432/{database}",
    )
    with pytest.raises(benchmark.BenchmarkFailure) as failure:
        benchmark.connections()
    assert failure.value.reason == 10
    for client in guarded_environment:
        client.assert_not_called()


@pytest.mark.parametrize(
    ("variable", "value", "reason"),
    [
        ("CELERY_BROKER_URL", "redis://aurum_api:synthetic@redis:6379/0", 11),
        ("CELERY_BROKER_URL", "redis://aurum_api:synthetic@localhost:6379/0", 11),
        ("CELERY_BROKER_URL", "redis://aurum_catalog:synthetic@benchmark-redis:6379/0", 11),
        ("CELERY_BROKER_URL", "redis://aurum_api:synthetic@benchmark-redis:6379/1", 11),
        ("CELERY_BROKER_URL", "redis://aurum_api:synthetic@benchmark-redis:16379/0", 11),
        ("CELERY_BROKER_URL", "redis://aurum_api@benchmark-redis:6379/0", 11),
        (
            "CELERY_BROKER_URL",
            "redis://aurum_api:synthetic@benchmark-redis:6379/0?host=redis",
            11,
        ),
        ("MINIO_ENDPOINT", "minio:9000", 12),
        ("MINIO_ENDPOINT", "localhost:9000", 12),
        ("MINIO_ENDPOINT", "benchmark-minio:9001", 12),
        ("MINIO_ENDPOINT", "benchmark-minio:9000/aurum", 12),
        ("MINIO_BUCKET", "aurum", 12),
        ("MINIO_BUCKET", "catalog-benchmark-copy", 12),
        ("MINIO_ROOT_PASSWORD", "", 12),
    ],
)
def test_nonbenchmark_broker_or_storage_refused_before_any_client(
    monkeypatch: pytest.MonkeyPatch,
    guarded_environment: tuple[Mock, ...],
    variable: str,
    value: str,
    reason: int,
) -> None:
    monkeypatch.setenv(variable, value)
    with pytest.raises(benchmark.BenchmarkFailure) as failure:
        benchmark.connections()
    assert failure.value.reason == reason
    for client in guarded_environment:
        client.assert_not_called()


@pytest.mark.parametrize("file_format", ["csv", "xlsx"])
@pytest.mark.parametrize("case", ["create", "skip", "update", "create_copy"])
def test_small_fixtures_parse_with_unique_valid_ean13_and_realistic_values(
    file_format: benchmark.FileFormat,
    case: benchmark.Case,
) -> None:
    count = 12
    raw = benchmark.fixture(count, file_format, case)
    assert 0 < len(raw) < MAX_IMPORT_FILE_BYTES
    rows, errors = parse_import(raw, f"synthetic.{file_format}")
    assert errors == []
    assert len(rows) == count
    codes = [str(row["barcode"]) for row in rows]
    assert len(set(codes)) == count
    for code in codes:
        assert len(code) == 13 and code.isascii() and code.isdigit()
        # Independent EAN13 validation, including the final check digit.
        assert (sum(map(int, code[::2])) + 3 * sum(map(int, code[1::2]))) % 10 == 0
    expected_price = Decimal("31.25") if case in ("skip", "update") else Decimal("21.50")
    for row in rows:
        assert row["base_price"] == expected_price
        assert row["inn"] == "Paracetamol"
        assert row["dosage"] == "500 mg"
        assert row["storage_type"] == "normal"
    base_rows, base_errors = parse_import(benchmark.fixture(count, "csv", "create"), "base.csv")
    assert base_errors == []
    base_codes = {str(row["barcode"]) for row in base_rows}
    if case == "create_copy":
        assert base_codes.isdisjoint(codes)
    else:
        assert base_codes == set(codes)


@pytest.mark.parametrize("case", ["create", "skip", "update", "create_copy"])
def test_csv_and_xlsx_produce_identical_typed_rows(case: benchmark.Case) -> None:
    csv_rows, csv_errors = parse_import(benchmark.fixture(3, "csv", case), "synthetic.csv")
    xlsx_rows, xlsx_errors = parse_import(benchmark.fixture(3, "xlsx", case), "synthetic.xlsx")
    assert csv_errors == xlsx_errors == []
    assert csv_rows == xlsx_rows
