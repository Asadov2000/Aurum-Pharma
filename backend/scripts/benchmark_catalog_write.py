"""Measure committed catalog imports through the real isolated Celery worker.

Use scripts/benchmark-catalog-write.ps1; this controller never starts workers,
changes limits, creates schema, deletes rows, or connects to the shared demo.
The runner provisions migration head, role ACLs, read-only worker storage and
the production catalog app with its unchanged 120/150 second task limits.
Each phase measures publication through observation in a fresh transaction,
including commit and polling latency. Fixture generation, upload and verification
are reported separately. This is a single-worker synthetic workload, not HTTP
latency, aggregate RSS, concurrent tenant capacity or external pilot approval.

Bootstrap alone uses aurum_support. Jobs and isolation probes use aurum_app;
the migrator connection is read-only and independently checks trigger audit.
Only the exact benchmark host/database/bucket identities below are accepted.
Default cases create, skip, update reuse the same valid EAN13 identifiers;
create_copy uses new unique barcodes for the same product duplicate keys.
JSON lines contain numeric measurements and fixed statuses only, never source
rows, object paths, identifiers, credentials or exception payloads.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import logging
import os
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import structlog
from minio import Minio
from openpyxl import Workbook
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from urllib3 import PoolManager, Timeout

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domains.auth.models import AppUser
from app.domains.catalog.import_limits import MAX_IMPORT_FILE_BYTES, catalog_execution_lock_key
from app.domains.catalog.import_parser import EXPECTED_COLUMNS, MAX_IMPORT_ROWS
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.domains.foundation.models import Tenant

Case = Literal["create", "skip", "update", "create_copy"]
FileFormat = Literal["csv", "xlsx"]
CASE_CODES: dict[str, int] = {"create": 1, "skip": 2, "update": 3, "create_copy": 4}
STATUS_CODES = {
    "pending": 1,
    "previewing": 2,
    "validating": 3,
    "importing": 4,
    "success": 5,
    "failed": 6,
    "rolled_back": 7,
}
PHASE_TIMEOUT_SECONDS = 170
DATABASE_NAME = "aurum_catalog_benchmark_test"
BUCKET_NAME = "catalog-benchmark"


class BenchmarkFailure(RuntimeError):
    """A fixed numeric failure reason, safe to emit without exception details."""

    def __init__(self, reason: int) -> None:
        super().__init__("Benchmark verification failed")
        self.reason = reason


def require(condition: bool, reason: int) -> None:
    if not condition:
        raise BenchmarkFailure(reason)


def emit(status: str, **metrics: int | float | str) -> None:
    print(json.dumps({"status": status, **metrics}, sort_keys=True), flush=True)


def database_url(variable: str, role: str) -> URL:
    value = make_url(os.environ[variable])
    require(
        value.drivername == "postgresql+asyncpg"
        and value.host == "benchmark-postgres"
        and value.port == 5432
        and value.database == DATABASE_NAME
        and value.username == role
        and bool(value.password)
        and not value.query,
        10,
    )
    return value


@dataclass(frozen=True)
class Connections:
    app: AsyncEngine
    support: AsyncEngine
    audit: AsyncEngine
    storage: Minio


def connections() -> Connections:
    # Validate EVERY destination before constructing clients or performing I/O.
    app = database_url("DATABASE_URL_APP", "aurum_app")
    support = database_url("DATABASE_URL_SUPPORT", "aurum_support")
    audit = database_url("DATABASE_URL_MIGRATION", "aurum_migrator")
    broker = urlsplit(os.environ["CELERY_BROKER_URL"])
    require(
        broker.scheme == "redis"
        and broker.hostname == "benchmark-redis"
        and broker.port == 6379
        and broker.username == "aurum_api"
        and bool(broker.password)
        and broker.path == "/0"
        and not broker.query
        and not broker.fragment,
        11,
    )
    require(
        os.environ["MINIO_ENDPOINT"] == "benchmark-minio:9000"
        and os.environ["MINIO_BUCKET"] == BUCKET_NAME
        and os.environ.get("MINIO_SECURE", "false").lower() == "false"
        and bool(os.environ["MINIO_ROOT_USER"])
        and bool(os.environ["MINIO_ROOT_PASSWORD"]),
        12,
    )
    engines = [
        create_async_engine(
            url,
            hide_parameters=True,
            pool_size=2,
            max_overflow=0,
            connect_args={"timeout": 10, "command_timeout": 15},
        )
        for url in (app, support, audit)
    ]
    return Connections(
        engines[0],
        engines[1],
        engines[2],
        storage=Minio(
            "benchmark-minio:9000",
            access_key=os.environ["MINIO_ROOT_USER"],
            secret_key=os.environ["MINIO_ROOT_PASSWORD"],
            secure=False,
            region="us-east-1",
            http_client=PoolManager(timeout=Timeout(connect=5, read=15), retries=False),
        ),
    )


@asynccontextmanager
async def session(
    engine: AsyncEngine,
    tenant_id: UUID | None = None,
    *,
    readonly: bool = False,
) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as db, db.begin():
        if readonly:
            await db.execute(text("SET TRANSACTION READ ONLY"))
        await db.execute(text("SELECT set_config('app.support_session', 'false', true)"))
        if tenant_id is not None:
            await db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
        yield db


async def verify_environment(env: Connections) -> None:
    for engine, role in (
        (env.app, "aurum_app"),
        (env.support, "aurum_support"),
        (env.audit, "aurum_migrator"),
    ):
        async with session(engine, readonly=True) as db:
            identity = (
                await db.execute(
                    text(
                        "SELECT current_database(), current_user, rolsuper, rolbypassrls "
                        "FROM pg_roles WHERE rolname = current_user"
                    )
                )
            ).one()
            require(identity[0] == DATABASE_NAME and identity[1] == role, 13)
            require(not identity[2], 14)
            if role == "aurum_app":
                require(not identity[3], 15)
                count = await db.scalar(
                    text(
                        "SELECT count(*) FROM pg_class c JOIN pg_namespace n "
                        "ON n.oid = c.relnamespace WHERE n.nspname = 'public' "
                        "AND c.relname IN ('tenant_catalog','barcode','catalog_import_job') "
                        "AND c.relrowsecurity"
                    )
                )
                require(count == 3, 16)
    require(await asyncio.to_thread(env.storage.bucket_exists, BUCKET_NAME), 17)


async def bootstrap(env: Connections) -> tuple[UUID, UUID, UUID]:
    tenant_id, foreign_id, user_id = uuid4(), uuid4(), uuid4()
    async with session(env.support) as db:
        await db.execute(text("SELECT set_config('app.support_session', 'true', true)"))
        db.add_all(
            [
                Tenant(
                    id=tenant_id,
                    name="Synthetic benchmark",
                    contact_email=f"{tenant_id}@example.invalid",
                ),
                Tenant(
                    id=foreign_id,
                    name="Isolation sentinel",
                    contact_email=f"{foreign_id}@example.invalid",
                ),
            ]
        )
        await db.flush()
        db.add(
            AppUser(
                id=user_id,
                email=f"{user_id}@example.invalid",
                full_name="Synthetic benchmark",
                home_tenant_id=tenant_id,
                status="active",
            )
        )
        await db.flush()
    # A populated foreign tenant catches cross-tenant reads and accidental writes
    # even when duplicate keys and barcode text match the imported first row.
    async with session(env.app, foreign_id) as db:
        service = CatalogService(CatalogRepository(db))
        fields: dict[str, object] = dict(
            zip(EXPECTED_COLUMNS, fixture_row(0, "create"), strict=True)
        )
        fields.pop("barcode")
        fields["base_price"] = Decimal("87.65")
        fields["category"] = "Foreign sentinel"
        item = await service.create_item(tenant_id=foreign_id, fields=fields)
        await service.add_barcode(
            tenant_id=foreign_id,
            catalog_id=item.id,
            code=barcode(0),
            code_type="ean13",
        )
    return tenant_id, foreign_id, user_id


def barcode(index: int, *, copy: bool = False) -> str:
    prefix = f"299{int(copy):01d}{index:08d}"
    checksum = (
        -sum(int(char) * (1 if pos % 2 == 0 else 3) for pos, char in enumerate(prefix))
    ) % 10
    return f"{prefix}{checksum}"


def fixture_row(index: int, case: Case) -> list[str]:
    changed = case in ("skip", "update")
    return [
        f"Synthetic medicine {index:06d}",
        "Paracetamol",
        "Synthetic manufacturer",
        "tablets",
        "500 mg",
        "20",
        "N02BE01",
        "otc",
        "normal",
        "Updated" if changed else "Analgesics",
        str(Decimal("31.25") if changed else Decimal("21.50")),
        barcode(index, copy=case == "create_copy"),
    ]


def fixture(rows: int, file_format: FileFormat, case: Case) -> bytes:
    if file_format == "csv":
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(EXPECTED_COLUMNS)
        writer.writerows(fixture_row(index, case) for index in range(rows))
        raw = stream.getvalue().encode("utf-8")
    else:
        output = io.BytesIO()
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet()
        sheet.append(EXPECTED_COLUMNS)
        for index in range(rows):
            sheet.append(fixture_row(index, case))
        workbook.save(output)
        workbook.close()
        raw = output.getvalue()
    require(len(raw) <= MAX_IMPORT_FILE_BYTES, 20)
    return raw


@dataclass(frozen=True)
class JobState:
    status: str
    total: int
    valid: int
    errors: int


async def observe(env: Connections, tenant_id: UUID, job_id: UUID) -> JobState:
    async with session(env.app, tenant_id, readonly=True) as db:
        job = await CatalogRepository(db).get_job(job_id)
        require(job is not None, 30)
        if job is None:
            raise BenchmarkFailure(30)
        return JobState(job.status, job.total_rows or 0, job.valid_rows or 0, job.error_rows or 0)


async def phase(
    env: Connections,
    tenant_id: UUID,
    job_id: UUID,
    case: Case,
    kind: Literal["preview", "import"],
    rows: int,
) -> float:
    from app.tasks.catalog_producer import enqueue_catalog_job

    async with session(env.app, tenant_id) as db:
        service = CatalogService(CatalogRepository(db))
        job = (
            await service.request_preview(job_id=job_id)
            if kind == "preview"
            else await service.confirm_import(
                job_id=job_id, duplicate_strategy="skip" if case == "create" else case
            )
        )
        execution_id = job.execution_id
        require(execution_id is not None, 31)
    if execution_id is None:
        raise BenchmarkFailure(31)
    started = time.perf_counter()
    state = JobState("pending", 0, 0, 0)
    polls = 0
    try:
        async with asyncio.timeout(PHASE_TIMEOUT_SECONDS):
            await asyncio.to_thread(enqueue_catalog_job, kind, job_id, tenant_id, execution_id)
            while True:
                state = await observe(env, tenant_id, job_id)
                polls += 1
                if state.status in ("validating", "success", "failed", "rolled_back"):
                    break
                await asyncio.sleep(0.25)
    except TimeoutError:
        emit(
            "timeout",
            case=CASE_CODES[case],
            phase=1 if kind == "preview" else 2,
            seconds=round(time.perf_counter() - started, 4),
            polls=polls,
            job_status=STATUS_CODES.get(state.status, 0),
            valid_rows=state.valid,
            error_rows=state.errors,
        )
        raise BenchmarkFailure(32) from None
    elapsed = time.perf_counter() - started
    expected = "validating" if kind == "preview" else "success"
    passed = (
        state.status == expected
        and state.total == rows
        and state.valid == rows
        and state.errors == 0
    )
    emit(
        "passed" if passed else "failed",
        case=CASE_CODES[case],
        phase=1 if kind == "preview" else 2,
        seconds=round(elapsed, 4),
        rows_per_second=round(rows / elapsed, 2),
        polls=polls,
        job_status=STATUS_CODES.get(state.status, 0),
        total_rows=state.total,
        valid_rows=state.valid,
        error_rows=state.errors,
    )
    require(passed, 33)
    return elapsed


@dataclass(frozen=True)
class Snapshot:
    catalog: int
    barcodes: int
    inserts: int
    updates: int
    identifiers: frozenset[UUID]
    barcode_links: frozenset[tuple[str, UUID, UUID]]
    persisted_digests: tuple[str, str, str]


async def persisted_digest(db: AsyncSession, table: str, tenant_id: UUID) -> str:
    """Hash complete committed rows; keep synthetic payloads out of output."""
    require(table in ("tenant_catalog", "barcode", "audit_log"), 53)
    value = await db.scalar(
        text(
            "SELECT md5(COALESCE(string_agg(to_jsonb(item)::text, '' ORDER BY item.id), '')) "
            f"FROM public.{table} item WHERE item.tenant_id = :tenant_id"
        ),
        {"tenant_id": tenant_id},
    )
    return str(value)


async def foreign_snapshot(env: Connections, foreign_id: UUID, target_id: UUID) -> str:
    """Compare all persisted sentinel fields, including metadata and audit rows."""
    async with session(env.app, foreign_id, readonly=True) as db:
        for table, expected in (("tenant_catalog", 1), ("barcode", 1), ("catalog_import_job", 0)):
            # The unfiltered count proves RLS still returns exactly the sentinel;
            # the explicit foreign predicate additionally probes target leakage.
            count = await db.scalar(text(f"SELECT count(*) FROM public.{table}"))
            require(count == expected, 47)
            target_count = await db.scalar(
                text(f"SELECT count(*) FROM public.{table} WHERE tenant_id = :target_id"),
                {"target_id": target_id},
            )
            require(target_count == 0, 47)
        value = await db.scalar(
            text(
                "SELECT jsonb_build_object("
                "'catalog', (SELECT jsonb_agg(to_jsonb(c) ORDER BY c.id) "
                "FROM public.tenant_catalog c), "
                "'barcodes', (SELECT jsonb_agg(to_jsonb(b) ORDER BY b.id) FROM public.barcode b), "
                "'jobs', (SELECT jsonb_agg(to_jsonb(j) ORDER BY j.id) "
                "FROM public.catalog_import_job j), "
                "'audit', (SELECT jsonb_agg(to_jsonb(a) ORDER BY a.id) FROM public.audit_log a)"
                ")::text"
            )
        )
    require(isinstance(value, str), 50)
    return str(value)


async def snapshot(
    env: Connections,
    tenant_id: UUID,
    rows: int,
    *,
    updated: bool,
    copies: bool,
) -> Snapshot:
    async with session(env.app, tenant_id, readonly=True) as db:
        records = (
            await db.execute(
                text(
                    "SELECT c.id, c.brand_name, c.inn, c.manufacturer, c.form, c.dosage, "
                    "c.pack_size, c.atx_code, c.dispensing_type, c.storage_type, c.category, "
                    "c.base_price, b.code, b.tenant_id, c.currency, c.is_active, "
                    "c.deleted_at, b.id "
                    "FROM public.tenant_catalog c LEFT JOIN public.barcode b "
                    "ON b.tenant_id = c.tenant_id AND b.catalog_id = c.id "
                    "ORDER BY c.brand_name, b.code"
                )
            )
        ).all()
        barcode_count = int(await db.scalar(text("SELECT count(*) FROM public.barcode")) or 0)
        catalog_digest = await persisted_digest(db, "tenant_catalog", tenant_id)
        barcode_digest = await persisted_digest(db, "barcode", tenant_id)
    multiplicity = 2 if copies else 1
    require(len(records) == rows * multiplicity and barcode_count == len(records), 40)
    expected_codes: set[str] = set()
    identifiers: set[UUID] = set()
    barcode_links: set[tuple[str, UUID, UUID]] = set()
    for record in records:
        code = str(record[12])
        index = int(str(record[1]).rsplit(" ", 1)[1])
        require(0 <= index < rows and code not in expected_codes, 41)
        is_copy = copies and code == barcode(index, copy=True)
        require(code == barcode(index, copy=is_copy), 42)
        expected = fixture_row(index, "update" if updated and not is_copy else "create")
        require(list(record[1:11]) == expected[:10], 43)
        require(record[11] == Decimal(expected[10]) and record[13] == tenant_id, 44)
        require(record[14] == "TJS" and record[15] is True and record[16] is None, 45)
        expected_codes.add(code)
        identifiers.add(record[0])
        barcode_links.add((code, record[17], record[0]))
    require(len(identifiers) == rows * multiplicity, 46)
    async with session(env.audit, readonly=True) as db:
        audit = (
            await db.execute(
                text(
                    "SELECT count(*) FILTER (WHERE action = 'INSERT'), "
                    "count(*) FILTER (WHERE action = 'UPDATE') FROM public.audit_log "
                    "WHERE tenant_id = :tenant_id AND table_name = 'tenant_catalog'"
                ),
                {"tenant_id": tenant_id},
            )
        ).one()
        audit_digest = await persisted_digest(db, "audit_log", tenant_id)
    return Snapshot(
        len(records),
        barcode_count,
        int(audit[0]),
        int(audit[1]),
        frozenset(identifiers),
        frozenset(barcode_links),
        (catalog_digest, barcode_digest, audit_digest),
    )


async def run_case(
    env: Connections,
    tenant_id: UUID,
    foreign_id: UUID,
    user_id: UUID,
    rows: int,
    file_format: FileFormat,
    case: Case,
    previous: Snapshot,
    sentinel: str,
    *,
    updated: bool,
) -> Snapshot:
    started = time.perf_counter()
    raw = fixture(rows, file_format, case)
    generated = time.perf_counter() - started
    key = f"{tenant_id}/imports/{uuid4()}.{file_format}"
    started = time.perf_counter()
    await asyncio.to_thread(env.storage.put_object, BUCKET_NAME, key, io.BytesIO(raw), len(raw))
    uploaded = time.perf_counter() - started
    async with session(env.app, tenant_id) as db:
        await db.execute(
            text("SELECT set_config('app.user_id', :user_id, true)"), {"user_id": str(user_id)}
        )
        job = await CatalogService(CatalogRepository(db)).create_import_job(
            tenant_id=tenant_id,
            user_id=user_id,
            source_filename=f"synthetic.{file_format}",
            source_path=f"{BUCKET_NAME}/{key}",
        )
        job_id = job.id
    emit(
        "prepared",
        case=CASE_CODES[case],
        rows=rows,
        bytes=len(raw),
        generation_seconds=round(generated, 4),
        upload_seconds=round(uploaded, 4),
    )
    await phase(env, tenant_id, job_id, case, "preview", rows)
    # Re-read immediately before publication so a later rollback assertion is
    # about this phase, including all metadata, rather than only row counts.
    before_import = await snapshot(env, tenant_id, previous.catalog, updated=updated, copies=False)
    require(before_import == previous, 54)
    try:
        await phase(env, tenant_id, job_id, case, "import", rows)
    except BenchmarkFailure:
        failed = await observe(env, tenant_id, job_id)
        # Hard-killed work can retain its durable claim until API recovery at
        # the 600s deadline. Prove the work lock is free before inspecting the
        # rollback; neither shorten that deadline nor call recovery in a timing
        # probe. Hold the lock while comparing committed data in other sessions.
        async with session(env.app, tenant_id) as db:
            lock_free = bool(
                await db.scalar(
                    text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": catalog_execution_lock_key(job_id)},
                )
            )
            emit(
                "failure_observed",
                execution_lock_free=int(lock_free),
                job_status=STATUS_CODES.get(failed.status, 0),
            )
            if lock_free and failed.status in ("failed", "importing"):
                after_failure = await snapshot(
                    env, tenant_id, previous.catalog, updated=updated, copies=False
                )
                require(after_failure == before_import, 55)
                require(await foreign_snapshot(env, foreign_id, tenant_id) == sentinel, 51)
                emit(
                    "rollback_verified",
                    case=CASE_CODES[case],
                    catalog_rows=after_failure.catalog,
                    barcode_rows=after_failure.barcodes,
                    audit_inserts=after_failure.inserts,
                    audit_updates=after_failure.updates,
                    job_status=STATUS_CODES[failed.status],
                )
        # A verified rollback is evidence of safe failure, never a passed import.
        raise
    started = time.perf_counter()
    current = await snapshot(
        env, tenant_id, rows, updated=updated or case == "update", copies=case == "create_copy"
    )
    require(await foreign_snapshot(env, foreign_id, tenant_id) == sentinel, 51)
    expected_inserts = rows * (2 if case == "create_copy" else 1)
    expected_updates = rows if updated or case == "update" else 0
    require(current.inserts == expected_inserts and current.updates == expected_updates, 48)
    if case in ("skip", "update"):
        require(
            current.identifiers == previous.identifiers
            and current.barcode_links == previous.barcode_links,
            49,
        )
    else:
        require(
            previous.identifiers < current.identifiers
            and previous.barcode_links < current.barcode_links,
            49,
        )
    emit(
        "verified",
        case=CASE_CODES[case],
        catalog_rows=current.catalog,
        barcode_rows=current.barcodes,
        audit_inserts=current.inserts,
        audit_updates=current.updates,
        foreign_visible_rows=0,
        foreign_preserved_rows=1,
        verification_seconds=round(time.perf_counter() - started, 4),
    )
    return current


async def run(rows: int, file_format: FileFormat, cases: list[Case]) -> None:
    env = connections()
    try:
        await verify_environment(env)
        tenant_id, foreign_id, user_id = await bootstrap(env)
        sentinel = await foreign_snapshot(env, foreign_id, tenant_id)
        previous = await snapshot(env, tenant_id, 0, updated=False, copies=False)
        require(previous.inserts == 0 and previous.updates == 0, 52)
        emit(
            "baseline_verified",
            catalog_rows=0,
            barcode_rows=0,
            audit_rows=0,
            foreign_preserved_rows=1,
        )
        updated = False
        for case in cases:
            previous = await run_case(
                env,
                tenant_id,
                foreign_id,
                user_id,
                rows,
                file_format,
                case,
                previous,
                sentinel,
                updated=updated,
            )
            updated = updated or case == "update"
        emit("passed", rows=rows, cases=len(cases), format=1 if file_format == "csv" else 2)
    finally:
        await env.app.dispose()
        await env.support.dispose()
        await env.audit.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=15000)
    parser.add_argument("--format", choices=("csv", "xlsx"), default="csv")
    parser.add_argument("--cases", default="create,skip,update")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))
    try:
        require(1 <= args.rows <= MAX_IMPORT_ROWS, 1)
        cases: list[Case] = []
        for value in args.cases.split(","):
            require(value in CASE_CODES, 2)
            if value in ("create", "skip", "update", "create_copy"):
                cases.append(value)
        require(bool(cases) and cases[0] == "create" and len(cases) == len(set(cases)), 3)
        require("create_copy" not in cases or cases[-1] == "create_copy", 4)
        require(
            "skip" not in cases
            or "update" not in cases
            or cases.index("skip") < cases.index("update"),
            5,
        )
        asyncio.run(run(args.rows, args.format, cases))
    except BenchmarkFailure as exc:
        emit("failed", reason=exc.reason)
        return 1
    except Exception as exc:
        emit("failed", reason=99, error_type=type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
