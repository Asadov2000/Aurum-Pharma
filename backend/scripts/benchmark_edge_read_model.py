"""Measure indexed Edge repository reads on an isolated PostgreSQL database.

Run only through ``scripts/benchmark-edge-read-model.ps1``.  The controller
accepts two fixed database identities and refuses every other destination before
constructing an engine. It creates synthetic projection rows in the disposable
database and measures repository queries plus response mapping in ``LocalReadModel``.

This is a repository microbenchmark. It deliberately supplies a test source
verifier and does not exercise signed snapshot installation, cryptographic
verification, active-pointer selection, HTTP or UI. The first observation is
only the first query in the run; later observations are repetitions. No result
from this script is an acceptance decision for ADR-0043.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings

if TYPE_CHECKING:
    from app.domains.sync.models import SyncReadSnapshot

DATABASE_NAME = "aurum_edge_read_benchmark_test"
DATABASE_HOST = "edge-benchmark-postgres"
TENANT_ID = UUID("51000000-0000-0000-0000-000000000001")
BRANCH_ID = UUID("51000000-0000-0000-0000-000000000002")
SNAPSHOT_ID = UUID("51000000-0000-0000-0000-000000000004")
CREDENTIAL_KID = UUID("51000000-0000-0000-0000-000000000005")
CREDENTIAL = f"edge_v1.{CREDENTIAL_KID}.{'5' * 64}"
FOREIGN_TENANT_ID = UUID("52000000-0000-0000-0000-000000000001")
FOREIGN_BRANCH_ID = UUID("52000000-0000-0000-0000-000000000002")
FOREIGN_NODE_ID = UUID("52000000-0000-0000-0000-000000000003")
TARGET_QUERY = "targetterm"
TARGET_BARCODE = "2999999999999"
STABILIZATION_IDLE_SECONDS = 30


class BenchmarkFailure(RuntimeError):
    """Fixed numeric failure reason safe for logs."""

    def __init__(self, reason: int) -> None:
        super().__init__("Edge read benchmark verification failed")
        self.reason = reason


def require(condition: bool, reason: int) -> None:
    if not condition:
        raise BenchmarkFailure(reason)


def database_url(variable: str, role: str) -> URL:
    value = make_url(os.environ[variable])
    require(
        value.drivername == "postgresql+asyncpg"
        and value.host == DATABASE_HOST
        and value.port == 5432
        and value.database == DATABASE_NAME
        and value.username == role
        and bool(value.password)
        and not value.query,
        10,
    )
    return value


@dataclass(frozen=True, slots=True)
class Connections:
    app: AsyncEngine
    support: AsyncEngine


def connections() -> Connections:
    """Validate every destination before constructing any I/O client."""
    app_url = database_url("DATABASE_URL_APP", "aurum_app")
    support_url = database_url("DATABASE_URL_SUPPORT", "aurum_support")
    engines = [
        create_async_engine(
            url,
            hide_parameters=True,
            pool_size=1,
            max_overflow=0,
            connect_args={"timeout": 10, "command_timeout": 1800},
        )
        for url in (app_url, support_url)
    ]
    return Connections(app=engines[0], support=engines[1])


@asynccontextmanager
async def session(
    engine: AsyncEngine,
    *,
    tenant_id: UUID | None = None,
    branch_id: UUID | None = None,
    node_id: UUID | None = None,
    support: bool = False,
    readonly: bool = False,
) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as db, db.begin():
        if readonly:
            await db.execute(text("SET TRANSACTION READ ONLY"))
        await db.execute(
            text("SELECT set_config('app.support_session', :value, true)"),
            {"value": "true" if support else "false"},
        )
        for key, value in (
            ("app.tenant_id", tenant_id),
            ("app.branch_id", branch_id),
            ("app.edge_node_id", node_id),
        ):
            if value is not None:
                await db.execute(
                    text("SELECT set_config(:key, :value, true)"),
                    {"key": key, "value": str(value)},
                )
        yield db


def foundation_payload() -> dict[str, object]:
    now = datetime.now(UTC).isoformat()
    return {
        "record_type": "foundation",
        "source_fingerprint": "f" * 64,
        "tenant": {
            "id": str(TENANT_ID),
            "name": "Synthetic Edge benchmark",
            "legal_name": None,
            "inn_or_tin": None,
            "registration_number": None,
            "legal_address": None,
            "status": "active",
            "drug_catalog_mode": "autonomous",
            "updated_at": now,
        },
        "settings": {
            "expiry_thresholds": {"yellow": 6, "orange": 3, "red": 1},
            "expired_sale_mode": "strict",
            "refund_reason_mode": "optional",
            "pos_payment_methods": ["cash"],
            "pos_mixed_payment_enabled": False,
            "draft_sale_lifetime_min": 30,
            "report_timezone": "Asia/Dushanbe",
            "prescription_warning_text": "Prescription required",
            "version": 1,
            "updated_at": now,
        },
        "branch": {
            "id": str(BRANCH_ID),
            "tenant_id": str(TENANT_ID),
            "name": "Synthetic branch",
            "address": None,
            "branch_type": "pharmacy",
            "license_number": None,
            "license_expires_at": None,
            "working_hours": None,
            "receipt_header": None,
            "is_active": True,
            "updated_at": now,
        },
        "registers": [],
    }


async def verify_environment(env: Connections) -> None:
    for engine, role, bypass in (
        (env.app, "aurum_app", False),
        (env.support, "aurum_support", True),
    ):
        async with session(engine, readonly=True) as db:
            row = (
                await db.execute(
                    text(
                        "SELECT current_database(), current_user, rolsuper, rolbypassrls "
                        "FROM pg_roles WHERE rolname = current_user"
                    )
                )
            ).one()
            require(row[0] == DATABASE_NAME and row[1] == role, 11)
            require(not row[2] and bool(row[3]) is bypass, 12)
    async with session(env.support, readonly=True) as db:
        projection_tables = await db.scalar(
            text(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relname IN "
                "('sync_read_projection','sync_read_catalog_projection',"
                "'sync_read_barcode_projection','sync_read_inventory_projection',"
                "'sync_read_inventory_rollup')"
            )
        )
        require(projection_tables == 5, 13)


async def seed(env: Connections, *, catalog_rows: int, inventory_rows: int) -> tuple[float, UUID]:
    started = time.perf_counter()
    foundation = json.dumps(foundation_payload(), separators=(",", ":"))
    async with session(env.support, support=True) as db:
        await db.execute(
            text(
                "INSERT INTO tenant (id,name,contact_email,status) "
                "VALUES (:tenant_id,'Synthetic Edge benchmark',"
                "'benchmark@example.invalid','active')"
            ),
            {"tenant_id": TENANT_ID},
        )
        await db.execute(
            text(
                "INSERT INTO branch (id,tenant_id,name) "
                "VALUES (:branch_id,:tenant_id,'Synthetic branch')"
            ),
            {"branch_id": BRANCH_ID, "tenant_id": TENANT_ID},
        )
        node_id = await db.scalar(
            text(
                "SELECT id FROM sync_node WHERE tenant_id=:tenant_id AND branch_id=:branch_id "
                "AND node_kind='cloud' AND mode='cloud_writer'"
            ),
            {"tenant_id": TENANT_ID, "branch_id": BRANCH_ID},
        )
        require(isinstance(node_id, UUID), 14)
    async with session(
        env.app,
        tenant_id=TENANT_ID,
        branch_id=BRANCH_ID,
        node_id=node_id,
    ) as db:
        await db.execute(
            text(
                "INSERT INTO sync_read_snapshot "
                "(snapshot_id,tenant_id,branch_id,node_id,status,schema_version,source_revision,"
                "credential_kid,manifest_hash,snapshot_hash,signature_algorithm,signature,"
                "source_generated_at) VALUES "
                "(:snapshot_id,:tenant_id,:branch_id,:node_id,'draft',1,1,:credential_kid,"
                ":digest,:digest,'hmac-sha256-edge-read-snapshot-v1',:digest,"
                "statement_timestamp())"
            ),
            {
                "snapshot_id": SNAPSHOT_ID,
                "tenant_id": TENANT_ID,
                "branch_id": BRANCH_ID,
                "node_id": node_id,
                "credential_kid": CREDENTIAL_KID,
                "digest": "a" * 64,
            },
        )
        await db.execute(
            text(
                "INSERT INTO sync_read_projection "
                "(snapshot_id,tenant_id,branch_id,node_id,source_revision,status,foundation_payload,"
                "catalog_count,supplier_count,inventory_count) VALUES "
                "(:snapshot_id,:tenant_id,:branch_id,:node_id,1,'building',"
                "CAST(:foundation AS jsonb),"
                ":catalog_rows,0,:inventory_rows)"
            ),
            {
                "tenant_id": TENANT_ID,
                "branch_id": BRANCH_ID,
                "node_id": node_id,
                "snapshot_id": SNAPSHOT_ID,
                "foundation": foundation,
                "catalog_rows": catalog_rows,
                "inventory_rows": inventory_rows,
            },
        )
        await db.execute(
            text(
                "INSERT INTO sync_read_catalog_projection "
                "(snapshot_id,catalog_id,tenant_id,branch_id,node_id,search_text,payload) "
                "SELECT CAST(:snapshot_id AS uuid), md5('catalog-' || value)::uuid, "
                "CAST(:tenant_id AS uuid), CAST(:branch_id AS uuid), "
                "CAST(:node_id AS uuid), '', jsonb_build_object("
                "'record_type','catalog_product','id',md5('catalog-' || value)::uuid,"
                "'tenant_id',CAST(:tenant_id AS uuid),'brand_name',"
                "CASE WHEN value=CAST(:catalog_rows AS integer) THEN "
                "'Product targetterm' ELSE 'Product ' || value END,'inn','medicine' || value,"
                "'manufacturer','Synthetic','form','tablet','dosage','500 mg','pack_size','10',"
                "'atx_code',NULL,'dispensing_type','otc','storage_type','normal','category','benchmark',"
                "'base_price','12.50','currency','TJS','image_version',NULL,'is_active',true,"
                "'created_at',statement_timestamp(),'updated_at',statement_timestamp(),"
                "'deleted_at',NULL,'barcodes',jsonb_build_array(jsonb_build_object("
                "'id',md5('barcode-' || value)::uuid,'code',CASE WHEN value=:catalog_rows THEN "
                "CAST(:target_barcode AS text) ELSE '29' || lpad(value::text,11,'0') END,"
                "'code_type','ean13'))) "
                "FROM generate_series(1,CAST(:catalog_rows AS integer)) value"
            ),
            {
                "snapshot_id": SNAPSHOT_ID,
                "tenant_id": TENANT_ID,
                "branch_id": BRANCH_ID,
                "node_id": node_id,
                "catalog_rows": catalog_rows,
                "target_barcode": TARGET_BARCODE,
            },
        )
        await db.execute(
            text(
                "INSERT INTO sync_read_barcode_projection "
                "(snapshot_id,barcode_id,catalog_id,tenant_id,branch_id,node_id,code,code_type) "
                "SELECT snapshot_id,(barcode->>'id')::uuid,catalog_id,tenant_id,branch_id,node_id,"
                "barcode->>'code',barcode->>'code_type' FROM sync_read_catalog_projection, "
                "LATERAL jsonb_array_elements(payload->'barcodes') barcode "
                "WHERE snapshot_id=:snapshot_id"
            ),
            {"snapshot_id": SNAPSHOT_ID},
        )
        await db.execute(
            text(
                "INSERT INTO sync_read_inventory_projection "
                "(snapshot_id,batch_id,tenant_id,branch_id,node_id,catalog_id,expires_at,"
                "qty_remaining,purchase_price,sale_price,is_blocked,payload) "
                "SELECT CAST(:snapshot_id AS uuid),md5('batch-' || value)::uuid,"
                "CAST(:tenant_id AS uuid),CAST(:branch_id AS uuid),CAST(:node_id AS uuid),"
                "md5('catalog-' || (((value-1) % CAST(:catalog_rows AS integer))+1))::uuid,"
                "(current_date + ((value % 730)+1)::int),"
                "CASE WHEN value % 10=0 THEN 0.000 ELSE 10.000 END,"
                "8.00,12.50,(value % 97=0),"
                "jsonb_build_object('record_type','inventory_batch','id',"
                "md5('batch-' || value)::uuid,"
                "'tenant_id',CAST(:tenant_id AS uuid),'branch_id',CAST(:branch_id AS uuid),"
                "'catalog_id',md5('catalog-' || "
                "(((value-1) % CAST(:catalog_rows AS integer))+1))::uuid,"
                "'batch_number','LOT-' || value,"
                "'manufactured_at',NULL,'expires_at',current_date + ((value % 730)+1)::int,"
                "'purchase_price','8.00','sale_price','12.50','currency','TJS','qty_initial','10.000',"
                "'qty_remaining',CASE WHEN value % 10=0 THEN '0.000' ELSE '10.000' END,"
                "'is_blocked',(value % 97=0),'block_reason',NULL,"
                "'blocked_at',NULL,'created_at',statement_timestamp(),'updated_at',statement_timestamp(),"
                "'branch_name','Synthetic branch','catalog_name',"
                "'Product ' || (((value-1) % CAST(:catalog_rows AS integer))+1),"
                "'catalog_form','tablet','catalog_dosage','500 mg','catalog_pack_size','10') "
                "FROM generate_series(1,CAST(:inventory_rows AS integer)) value"
            ),
            {
                "snapshot_id": SNAPSHOT_ID,
                "tenant_id": TENANT_ID,
                "branch_id": BRANCH_ID,
                "node_id": node_id,
                "catalog_rows": catalog_rows,
                "inventory_rows": inventory_rows,
            },
        )
        await db.execute(
            text(
                "INSERT INTO sync_read_inventory_rollup "
                "(snapshot_id,tenant_id,branch_id,node_id,has_stock,expires_at,batch_count,"
                "total_qty,purchase_value,sale_value,blocked_count) "
                "SELECT snapshot_id,tenant_id,branch_id,node_id,qty_remaining>0,expires_at,"
                "count(*),sum(qty_remaining),sum(qty_remaining*purchase_price),"
                "sum(qty_remaining*sale_price),count(*) FILTER (WHERE is_blocked) "
                "FROM sync_read_inventory_projection WHERE snapshot_id=:snapshot_id "
                "GROUP BY snapshot_id,tenant_id,branch_id,node_id,qty_remaining>0,expires_at"
            ),
            {"snapshot_id": SNAPSHOT_ID},
        )
    async with session(
        env.app,
        tenant_id=TENANT_ID,
        branch_id=BRANCH_ID,
        node_id=node_id,
    ) as db:
        await db.execute(text("ANALYZE sync_read_catalog_projection"))
        await db.execute(text("ANALYZE sync_read_barcode_projection"))
        await db.execute(text("ANALYZE sync_read_inventory_projection"))
        await db.execute(text("ANALYZE sync_read_inventory_rollup"))
    return time.perf_counter() - started, node_id


def negative_rls_scopes(node_id: UUID) -> tuple[tuple[UUID, UUID, UUID], ...]:
    """Change one RLS coordinate at a time to catch partial-scope policies."""
    return (
        (FOREIGN_TENANT_ID, BRANCH_ID, node_id),
        (TENANT_ID, FOREIGN_BRANCH_ID, node_id),
        (TENANT_ID, BRANCH_ID, FOREIGN_NODE_ID),
    )


async def visible_projection_rows(
    env: Connections, *, tenant_id: UUID, branch_id: UUID, node_id: UUID
) -> int:
    async with session(
        env.app,
        tenant_id=tenant_id,
        branch_id=branch_id,
        node_id=node_id,
        readonly=True,
    ) as db:
        value = await db.scalar(
            text(
                "SELECT "
                "(SELECT count(*) FROM sync_read_projection WHERE snapshot_id=:snapshot_id) + "
                "(SELECT count(*) FROM sync_read_catalog_projection "
                " WHERE snapshot_id=:snapshot_id) + "
                "(SELECT count(*) FROM sync_read_barcode_projection "
                " WHERE snapshot_id=:snapshot_id) + "
                "(SELECT count(*) FROM sync_read_inventory_projection "
                " WHERE snapshot_id=:snapshot_id) + "
                "(SELECT count(*) FROM sync_read_inventory_rollup "
                " WHERE snapshot_id=:snapshot_id)"
            ),
            {"snapshot_id": SNAPSHOT_ID},
        )
        return int(value or 0)


async def verify_negative_rls(
    env: Connections, *, node_id: UUID, catalog_rows: int, inventory_rows: int
) -> int:
    visible = await visible_projection_rows(
        env,
        tenant_id=TENANT_ID,
        branch_id=BRANCH_ID,
        node_id=node_id,
    )
    require(visible == 1 + (catalog_rows * 2) + inventory_rows + min(inventory_rows, 730), 15)
    scopes = negative_rls_scopes(node_id)
    for tenant_id, branch_id, scoped_node_id in scopes:
        hidden = await visible_projection_rows(
            env,
            tenant_id=tenant_id,
            branch_id=branch_id,
            node_id=scoped_node_id,
        )
        require(hidden == 0, 16)
    return len(scopes)


class BenchmarkRepository:
    def __init__(self, db: AsyncSession, foundation: dict[str, object]) -> None:
        from app.domains.sync.repository import SyncEdgeRepository

        self._delegate = SyncEdgeRepository(db)
        self._foundation = foundation

    def __getattr__(self, name: str) -> object:
        return getattr(self._delegate, name)

    async def get_active_read_projection(
        self, **_kwargs: object
    ) -> tuple[object, object, datetime]:
        now = datetime.now(UTC)
        return (
            SimpleNamespace(
                snapshot_id=SNAPSHOT_ID,
                signature="a" * 64,
                source_revision=1,
                source_generated_at=now,
            ),
            SimpleNamespace(foundation_payload=self._foundation),
            now,
        )


async def skip_source_verification_for_microbenchmark(
    _snapshot: SyncReadSnapshot, _credential: str
) -> None:
    return None


def percentile(values: Sequence[float], fraction: float) -> float:
    require(bool(values), 20)
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * fraction + 0.999999) - 1))
    return ordered[index]


def summarize(values: Sequence[float]) -> dict[str, float | int]:
    return {
        "samples": len(values),
        "p50_ms": round(statistics.median(values), 3),
        "p95_ms": round(percentile(values, 0.95), 3),
        "max_ms": round(max(values), 3),
    }


def benchmark_claims() -> dict[str, object]:
    return {
        "reference_budgets_ms": {
            "catalog_search_p95_ms": {"value": 300, "kind": "adr_0043_direct"},
            "exact_barcode_p95_ms": {"value": 150, "kind": "diagnostic_component"},
            "inventory_p95_ms": {"value": 300, "kind": "diagnostic_reference"},
        },
        "acceptance_claimed": False,
    }


async def measure(
    call: Callable[[], Awaitable[object]], repetitions: int
) -> tuple[float, list[float]]:
    values: list[float] = []
    for _ in range(repetitions + 1):
        started = time.perf_counter_ns()
        await call()
        values.append((time.perf_counter_ns() - started) / 1_000_000)
    return values[0], values[1:]


async def probe_reused_connection(
    env: Connections,
    *,
    node_id: UUID,
) -> dict[str, object]:
    from app.domains.sync.repository import SyncEdgeRepository

    async with session(
        env.app,
        tenant_id=TENANT_ID,
        branch_id=BRANCH_ID,
        node_id=node_id,
        readonly=True,
    ) as reused_db:
        repository = SyncEdgeRepository(reused_db)
        diagnostic_today = datetime.now(UTC).date()

        def inventory_call(show_empty: bool) -> Awaitable[object]:
            return repository.list_read_inventory_projection(
                snapshot_id=SNAPSHOT_ID,
                show_empty=show_empty,
                offset=0,
                limit=20,
                today=diagnostic_today,
                orange_until=diagnostic_today + timedelta(days=90),
            )

        metrics: dict[str, object] = {}
        for show_empty in (False, True):
            suffix = "all" if show_empty else "available"
            call = partial(inventory_call, show_empty)
            default_ms, _ = await measure(call, 0)
            await reused_db.execute(text("SET LOCAL plan_cache_mode = 'force_custom_plan'"))
            custom_ms, _ = await measure(call, 0)
            await reused_db.execute(text("SET LOCAL plan_cache_mode = 'auto'"))
            auto_after_ms, _ = await measure(call, 0)
            metrics[suffix] = {
                "default_ms": round(default_ms, 3),
                "force_custom_ms": round(custom_ms, 3),
                "auto_after_ms": round(auto_after_ms, 3),
            }
    return metrics


async def benchmark(
    env: Connections,
    *,
    repetitions: int,
    node_id: UUID,
    catalog_rows: int,
    inventory_rows: int,
) -> dict[str, object]:
    os.environ["EDGE_SYNC_CREDENTIAL"] = CREDENTIAL
    os.environ["EDGE_READ_SNAPSHOT_STALE_SECONDS"] = "86400"
    os.environ.setdefault("JWT_SECRET", "b" * 64)
    get_settings.cache_clear()
    from app.domains.sync.read_model import LocalReadModel
    from app.domains.sync.repository import SyncEdgeRepository

    stabilization_started = time.perf_counter()
    await asyncio.sleep(STABILIZATION_IDLE_SECONDS)
    stabilization_seconds = time.perf_counter() - stabilization_started
    reused_connection_probe = await probe_reused_connection(env, node_id=node_id)
    await env.app.dispose()
    foundation = foundation_payload()
    async with session(
        env.app,
        tenant_id=TENANT_ID,
        branch_id=BRANCH_ID,
        node_id=node_id,
        readonly=True,
    ) as db:
        service = LocalReadModel(
            cast(SyncEdgeRepository, BenchmarkRepository(db, foundation)),
            source_verifier=skip_source_verification_for_microbenchmark,
        )
        expected_catalog_id = UUID(
            hashlib.md5(f"catalog-{catalog_rows}".encode(), usedforsecurity=False).hexdigest()
        )
        catalog_result = await service.catalog(
            tenant_id=TENANT_ID,
            branch_id=BRANCH_ID,
            query=TARGET_QUERY,
            page=1,
            page_size=20,
        )
        require(
            catalog_result.total == 1
            and len(catalog_result.items) == 1
            and catalog_result.items[0].id == expected_catalog_id,
            40,
        )
        barcode_result = await service.catalog_details(
            tenant_id=TENANT_ID,
            branch_id=BRANCH_ID,
            barcode=TARGET_BARCODE,
        )
        require(barcode_result.item.id == expected_catalog_id, 41)

        expected_available = inventory_rows - (inventory_rows // 10)
        expected_qty = Decimal(expected_available * 10)
        expected_purchase_value = Decimal(expected_available * 80)
        expected_sale_value = Decimal(expected_available * 125)
        inventory_result = await service.inventory(
            tenant_id=TENANT_ID,
            branch_id=BRANCH_ID,
            can_view_costs=True,
            show_empty=False,
            page=1,
            page_size=20,
        )
        require(inventory_result.total == expected_available, 42)
        require(len(inventory_result.items) == min(20, expected_available), 43)
        require(inventory_result.summary.total_qty == expected_qty, 44)
        require(inventory_result.summary.purchase_value == expected_purchase_value, 45)
        require(inventory_result.summary.sale_value == expected_sale_value, 46)
        require(
            inventory_result.summary.blocked_count
            == (inventory_rows // 97) - (inventory_rows // 970),
            47,
        )
        inventory_all_result = await service.inventory(
            tenant_id=TENANT_ID,
            branch_id=BRANCH_ID,
            can_view_costs=True,
            show_empty=True,
            page=1,
            page_size=20,
        )
        require(inventory_all_result.total == inventory_rows, 48)
        require(len(inventory_all_result.items) == min(20, inventory_rows), 49)
        require(inventory_all_result.summary.total_qty == expected_qty, 50)
        require(inventory_all_result.summary.purchase_value == expected_purchase_value, 51)
        require(inventory_all_result.summary.sale_value == expected_sale_value, 52)
        require(inventory_all_result.summary.blocked_count == inventory_rows // 97, 53)
        catalog_cold, catalog_warm = await measure(
            lambda: service.catalog(
                tenant_id=TENANT_ID,
                branch_id=BRANCH_ID,
                query=TARGET_QUERY,
                page=1,
                page_size=20,
            ),
            repetitions,
        )
        barcode_cold, barcode_warm = await measure(
            lambda: service.catalog_details(
                tenant_id=TENANT_ID,
                branch_id=BRANCH_ID,
                barcode=TARGET_BARCODE,
            ),
            repetitions,
        )
        inventory_cold, inventory_warm = await measure(
            lambda: service.inventory(
                tenant_id=TENANT_ID,
                branch_id=BRANCH_ID,
                can_view_costs=True,
                show_empty=False,
                page=1,
                page_size=20,
            ),
            repetitions,
        )
        inventory_all_cold, inventory_all_warm = await measure(
            lambda: service.inventory(
                tenant_id=TENANT_ID,
                branch_id=BRANCH_ID,
                can_view_costs=True,
                show_empty=True,
                page=1,
                page_size=20,
            ),
            repetitions,
        )
    metrics: dict[str, object] = {
        "catalog_search": {"first_query_ms": round(catalog_cold, 3), **summarize(catalog_warm)},
        "exact_barcode": {"first_query_ms": round(barcode_cold, 3), **summarize(barcode_warm)},
        "inventory": {"first_query_ms": round(inventory_cold, 3), **summarize(inventory_warm)},
        "inventory_with_empty": {
            "first_query_ms": round(inventory_all_cold, 3),
            **summarize(inventory_all_warm),
        },
    }
    return {
        "metrics": metrics,
        "stabilization": {
            "idle_seconds": STABILIZATION_IDLE_SECONDS,
            "actual_seconds": round(stabilization_seconds, 3),
            "reused_connection_probe_ms": reused_connection_probe,
        },
        **benchmark_claims(),
    }


async def run(args: argparse.Namespace) -> None:
    require(args.catalog_rows >= 1 and args.inventory_rows >= 1, 30)
    require(args.repetitions >= 5, 31)
    env = connections()
    try:
        await verify_environment(env)
        seed_seconds, node_id = await seed(
            env,
            catalog_rows=args.catalog_rows,
            inventory_rows=args.inventory_rows,
        )
        negative_scopes_verified = await verify_negative_rls(
            env,
            node_id=node_id,
            catalog_rows=args.catalog_rows,
            inventory_rows=args.inventory_rows,
        )
        result = await benchmark(
            env,
            repetitions=args.repetitions,
            node_id=node_id,
            catalog_rows=args.catalog_rows,
            inventory_rows=args.inventory_rows,
        )
        print(
            json.dumps(
                {
                    "status": "success",
                    "benchmark": "edge_read_model",
                    "catalog_rows": args.catalog_rows,
                    "inventory_rows": args.inventory_rows,
                    "repetitions": args.repetitions,
                    "seed_seconds": round(seed_seconds, 3),
                    "negative_rls_scopes_verified": negative_scopes_verified,
                    **result,
                },
                sort_keys=True,
            ),
            flush=True,
        )
    finally:
        await env.app.dispose()
        await env.support.dispose()
        get_settings.cache_clear()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog-rows", type=int, default=100_000)
    parser.add_argument("--inventory-rows", type=int, default=250_000)
    parser.add_argument("--repetitions", type=int, default=30)
    return parser.parse_args()


if __name__ == "__main__":
    try:
        asyncio.run(run(parse_args()))
    except BenchmarkFailure as exc:
        print(json.dumps({"status": "failed", "reason": exc.reason}), flush=True)
        raise SystemExit(exc.reason) from None
