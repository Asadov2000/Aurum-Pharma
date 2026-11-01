"""CI-only native replay proof with explicitly synthetic completed-command anchors.

The completed checkout is created by the real aurum_app service and guards.
Maintenance provisions the original native identity/epoch/anchor afterward only
as a test fixture: this does NOT prove native monetary execution or a producer.
No completed Sale is rewritten. All fixture data dies with the disposable CI DB.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from app.domains.pos.edge_retail_cash import (
        EdgeRetailCashCommandV1,
        EdgeRetailCashKind,
    )
    from app.domains.pos.edge_retail_cash_replay import EdgeRetailCashReplayV1
    from app.domains.pos.mixed_documents import MixedCheckoutResultV4
    from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
    from sqlalchemy.engine import URL
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

type Checkout = PhysicalCheckoutResultV3 | MixedCheckoutResultV4


@dataclass
class DiagnosticState:
    phase: str = "entry_guard"


def require_ci_context() -> None:
    """Reject unsafe invocation before imports can load config or runtime engines."""
    if (
        os.getenv("CI") != "true"
        or os.getenv("CIRCLECI") != "true"
        or os.getenv("AURUM_DISPOSABLE_DB_CONFIRM") != "destroy-this-database"
    ):
        raise RuntimeError("Native replay proof requires explicitly disposable CircleCI")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", os.getenv("POSTGRES_SERVICE_ID", "")):
        raise RuntimeError("Invalid disposable PostgreSQL container identifier")
    targets: set[tuple[str, int, str]] = set()
    for name, username in (
        ("DATABASE_URL_APP", "aurum_app"),
        ("DATABASE_URL_SUPPORT", "aurum_support"),
        ("DATABASE_URL_MIGRATION", "aurum_migrator"),
    ):
        try:
            parsed = urlsplit(os.getenv(name, ""))
            port = parsed.port if parsed.port is not None else 5432
            if (
                parsed.scheme != "postgresql+asyncpg"
                or parsed.hostname not in {"localhost", "127.0.0.1"}
                or parsed.username != username
                or port < 1
                or not re.fullmatch(r"/[A-Za-z0-9_]+_test", parsed.path)
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("invalid disposable target")
        except ValueError:
            raise RuntimeError("Native replay requires explicit local test-role URLs") from None
        targets.add((parsed.hostname, port, parsed.path))
    if len(targets) != 1:
        raise RuntimeError(
            "Replay fixture engines must share one local host port and test database"
        )


class FixtureAdminError(RuntimeError):
    """Only an allowlisted SQLSTATE crosses the privileged fixture boundary."""

    def __init__(self, sqlstate: str | None) -> None:
        super().__init__("Disposable replay fixture admin SQL failed")
        self.sqlstate = sqlstate


def literal(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def ci_database_url(value: str) -> URL:
    from sqlalchemy.engine import make_url

    parsed = make_url(value)
    if (
        parsed.drivername != "postgresql+asyncpg"
        or parsed.host not in {"localhost", "127.0.0.1"}
        or not parsed.database
        or not parsed.database.endswith("_test")
    ):
        raise RuntimeError("Native replay requires a local disposable PostgreSQL test database")
    return parsed


@dataclass(frozen=True)
class Admin:
    container: str
    database: str

    def run(self, sql: str) -> str:
        # No generated credential or application row is passed on argv or printed.
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                self.container,
                "psql",
                "-qAt",
                "-U",
                "postgres",
                "-d",
                self.database,
                "-v",
                "ON_ERROR_STOP=1",
                "-v",
                "VERBOSITY=sqlstate",
            ],
            input=sql,
            text=True,
            capture_output=True,
            check=False,
            timeout=45,
        )
        if result.returncode:
            codes = re.findall(r"ERROR:\s+([A-Z0-9]{5})\b", result.stderr)
            raise FixtureAdminError(codes[-1] if codes else None)
        return result.stdout.strip()

    def snapshot(self, tenant: UUID) -> str:
        tables = (
            "sale",
            "sale_item",
            "sale_payment",
            "shift",
            "batch",
            "batch_movement",
            "pos_physical_checkout",
            "pos_mixed_checkout",
            "pos_unit_cash_intent",
            "pos_unit_cart_item",
            "pos_unit_checkout_payment_component",
            "unit_stock_lot_v2",
            "unit_stock_opened_pack_v2",
            "unit_stock_cart_command_v2",
            "unit_stock_cart_line_v2",
            "unit_stock_movement_v2",
            "edge_cash_command",
            "sync_stream",
            "sync_node",
            "sync_outbox",
            "sync_writer_epoch",
            "sync_writer_activation",
            "pos_retail_checkout_origin",
            "pos_retail_checkout_event",
            "audit_log",
        )
        queries = [
            f"SELECT {literal(table)} AS kind,to_jsonb(row) AS body FROM public.{table} row "
            f"WHERE tenant_id={literal(tenant)}::UUID"
            for table in tables
        ]
        queries.append(
            "SELECT 'register_receipt_counter',to_jsonb(counter) "
            "FROM public.register_receipt_counter counter JOIN public.register register "
            "ON register.id=counter.register_id "
            f"WHERE register.tenant_id={literal(tenant)}::UUID"
        )
        return self.run(
            "WITH rows AS (" + " UNION ALL ".join(queries) + ") "
            "SELECT md5(coalesce(jsonb_agg(jsonb_build_array(kind,body) "
            "ORDER BY kind,body::TEXT)::TEXT,'[]')) FROM rows;"
        )


@dataclass(frozen=True)
class Fixture:
    command: EdgeRetailCashCommandV1
    result: Checkout
    node_role: str
    password: str = field(repr=False)

    def wrapper(self) -> EdgeRetailCashReplayV1:
        from app.domains.pos.edge_retail_cash_replay import EdgeRetailCashReplayV1

        return EdgeRetailCashReplayV1(
            profile="retail_cash.completed.v1",
            command_type="sale.cash.complete",
            kind=self.command.kind,
            scope=self.command.scope,
            confirmation=self.command.confirmation,
            result=self.result,
            operation_id=self.result.operation_id,
            sale_id=self.result.sale_id,
            receipt_number=self.result.receipt_number,
            total_amount=self.result.total_amount,
            currency="TJS",
        )


async def completed_fixture(
    support: AsyncEngine,
    maintenance: AsyncEngine,
    factory: async_sessionmaker[AsyncSession],
    kind: EdgeRetailCashKind,
) -> Fixture:
    from app.domains.pos.edge_retail_cash import EdgeRetailCashCommandV1
    from app.domains.pos.mixed_cash_checkout_repository import (
        MixedCashCheckoutRepository,
    )
    from app.domains.pos.mixed_cash_checkout_service import MixedCashCheckoutService
    from app.domains.pos.physical_cash_checkout_repository import (
        PhysicalCashCheckoutRepository,
    )
    from app.domains.pos.physical_cash_checkout_service import (
        PhysicalCashCheckoutService,
    )
    from app.domains.pos.repository import POSRepository
    from app.domains.pos.service import POSService
    from app.domains.sync.schemas import OfflineAuthScopeV0
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession
    from tests.domains.incoming.test_unit_acceptance import (
        ReceivingRuntime,
        seed_receiving_actor,
        set_receiving_context,
    )
    from tests.domains.inventory.test_unit_ledger import seed_ledger_context
    from tests.domains.pos.test_mixed_physical_checkout_preparation import (
        legacy_with_stock,
    )
    from tests.domains.pos.test_unit_physical_cart import put_physical
    from tests.domains.pos.test_unit_physical_checkout_preparation import (
        checkout_source,
        saved_request,
    )

    async with AsyncSession(support) as session, session.begin():
        await session.execute(text("SELECT set_config('app.support_session','true',true)"))
        stock = await seed_ledger_context(session)
    actor = await seed_receiving_actor(support, maintenance, stock)
    async with factory() as session:
        session.info["receiving_runtime"] = ReceivingRuntime(factory, stock, actor)
        await set_receiving_context(session, stock, actor)
        source = await checkout_source(session)
        if kind == "mixed_cash":
            await legacy_with_stock(session, source.sale)
        await put_physical(session, source.sale.id)
        request = await saved_request(session, source.sale)
        # Set this native-ledger FK input while still draft, never after completion.
        await session.execute(
            text(
                "UPDATE public.sale SET operation_id=:operation WHERE id=:sale AND status='draft'"
            ),
            {"operation": request.operation_id, "sale": source.sale.id},
        )
        result: Checkout
        if kind == "physical_cash":
            result = await PhysicalCashCheckoutService(
                PhysicalCashCheckoutRepository(session)
            ).complete(
                tenant_id=stock.tenant_id,
                actor_id=actor,
                allowed_branch_ids={stock.branch_id},
                request=request,
            )
        else:
            result = await MixedCashCheckoutService(MixedCashCheckoutRepository(session)).complete(
                tenant_id=stock.tenant_id,
                actor_id=actor,
                allowed_branch_ids={stock.branch_id},
                request=request,
            )
        await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        await session.commit()
        await set_receiving_context(session, stock, actor)
        await POSService(POSRepository(session)).close_shift(
            shift_id=result.shift_id,
            closing_cash_actual=result.total_amount,
            closed_by_user_id=actor,
            allowed_branch_ids={stock.branch_id},
        )
        await session.commit()
    scope = OfflineAuthScopeV0(
        activation_id=uuid4(),
        tenant_id=stock.tenant_id,
        branch_id=stock.branch_id,
        edge_node_id=uuid4(),
        register_id=result.register_id,
        writer_epoch=2,
        user_id=actor,
        capability="cash_sale_v1",
    )
    return Fixture(
        EdgeRetailCashCommandV1(kind=kind, scope=scope, confirmation=request),
        result,
        "aurum_edge_node_" + scope.edge_node_id.hex,
        secrets.token_hex(32),
    )


def provision_identity(admin: Admin, fixture: Fixture) -> None:
    scope, role = fixture.command.scope, identifier(fixture.node_role)
    # Only trusted synthetic node/activation fixture triggers are suspended.
    # Current stream remains cloud_full; epoch2 is historical and fenced already.
    admin.run(f"""
BEGIN;
ALTER TABLE public.sync_node DISABLE TRIGGER USER;
ALTER TABLE public.sync_writer_activation DISABLE TRIGGER USER;
ALTER TABLE public.sync_writer_epoch DISABLE TRIGGER USER;
INSERT INTO public.sync_node(
 id,tenant_id,branch_id,register_id,node_kind,mode,status,display_name,
 credential_kid,credential_hash,credential_issued_at,credential_expires_at,
 shadow_start_origin_node_id,shadow_start_writer_epoch,shadow_start_sequence,
 shadow_start_checksum,shadow_start_projection_checksum)
SELECT {literal(scope.edge_node_id)},{literal(scope.tenant_id)},{literal(scope.branch_id)},
 {literal(scope.register_id)},'edge','edge_writer','active','Synthetic replay fixture',
 gen_random_uuid(),encode(gen_random_bytes(32),'hex'),now(),now()+interval '1 day',
 writer_node_id,writer_epoch,0,repeat('0',64),repeat('0',64)
FROM public.sync_stream WHERE tenant_id={literal(scope.tenant_id)}
 AND branch_id={literal(scope.branch_id)};
INSERT INTO public.sync_writer_activation(
 activation_id,tenant_id,branch_id,writer_epoch,writer_node_id,allowed_register_id,
 capability,state,root_source_checksum,root_projection_checksum,last_sequence,
 current_source_checksum,current_projection_checksum,previous_writer_epoch,
 previous_terminal_sequence,previous_terminal_source_checksum,
 previous_terminal_projection_checksum,bootstrap_snapshot_hash,activation_manifest_hash,
 receipt_baseline_seq,prepare_request_hash,prepared_at,ready_at,activated_at)
VALUES({literal(scope.activation_id)},{literal(scope.tenant_id)},{literal(scope.branch_id)},
 2,{literal(scope.edge_node_id)},{literal(scope.register_id)},'cash_sale_v1','activated',
 repeat('0',64),repeat('0',64),0,repeat('0',64),repeat('0',64),1,0,
 repeat('0',64),repeat('0',64),repeat('0',64),repeat('0',64),0,repeat('d',64),now(),now(),now());
INSERT INTO public.sync_writer_epoch(
 tenant_id,branch_id,writer_epoch,activation_id,writer_node_id,allowed_register_id,
 capability,state,root_source_checksum,root_projection_checksum,last_sequence,
 current_source_checksum,current_projection_checksum,previous_writer_epoch,
 previous_terminal_sequence,previous_terminal_source_checksum,
 previous_terminal_projection_checksum,bootstrap_snapshot_hash,activation_manifest_hash,
 receipt_baseline_seq,prepared_at,activated_at,fenced_at)
VALUES({literal(scope.tenant_id)},{literal(scope.branch_id)},2,{literal(scope.activation_id)},
 {literal(scope.edge_node_id)},{literal(scope.register_id)},'cash_sale_v1','fenced',
 repeat('0',64),repeat('0',64),0,repeat('0',64),repeat('0',64),1,0,
 repeat('0',64),repeat('0',64),repeat('0',64),repeat('0',64),0,now(),now(),now());
SET CONSTRAINTS ALL IMMEDIATE;
ALTER TABLE public.sync_writer_epoch ENABLE TRIGGER USER;
ALTER TABLE public.sync_writer_activation ENABLE TRIGGER USER;
ALTER TABLE public.sync_node ENABLE TRIGGER USER;
CREATE ROLE {role} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
 NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 1 PASSWORD {literal(fixture.password)};
GRANT aurum_edge_cash_executor TO {role} WITH ADMIN FALSE,INHERIT TRUE,SET FALSE;
GRANT CONNECT ON DATABASE {identifier(admin.database)} TO {role};
INSERT INTO public.edge_cash_node_identity(
 tenant_id,branch_id,edge_node_id,register_id,database_role,database_role_oid)
SELECT {literal(scope.tenant_id)},{literal(scope.branch_id)},{literal(scope.edge_node_id)},
 {literal(scope.register_id)},{literal(fixture.node_role)},oid
FROM pg_catalog.pg_roles WHERE rolname={literal(fixture.node_role)};
COMMIT;
""")


def provision_anchor(admin: Admin, fixture: Fixture, *, corrupt_hash: bool = False) -> None:
    from app.domains.pos.edge_retail_cash_replay import completed_command_request_hash

    scope, result = fixture.command.scope, fixture.result
    wrapper = canonical(fixture.wrapper().model_dump(mode="json"))
    result_hash = "0" * 64 if corrupt_hash else hashlib.sha256(wrapper.encode()).hexdigest()
    admin.run(f"""
BEGIN;
SELECT set_config('app.user_id',{literal(scope.user_id)},true) AS actor \\gset
INSERT INTO public.edge_cash_command(
 tenant_id,branch_id,operation_id,edge_identity_id,activation_id,edge_node_id,
 writer_epoch,register_id,cashier_user_id,sale_id,sale_status,receipt_number,
 total_amount,currency,command_type,request_hash,result_payload,result_hash,created_by)
SELECT {literal(scope.tenant_id)},{literal(scope.branch_id)},{literal(result.operation_id)},id,
 {literal(scope.activation_id)},{literal(scope.edge_node_id)},2,{literal(scope.register_id)},
 {literal(scope.user_id)},{literal(result.sale_id)},'completed',{literal(result.receipt_number)},
 {literal(result.total_amount)},'TJS','sale.cash.complete',
 {literal(completed_command_request_hash(fixture.command))},
 {literal(wrapper)}::JSONB,{literal(result_hash)},{literal(scope.user_id)}
FROM public.edge_cash_node_identity WHERE database_role={literal(fixture.node_role)};
COMMIT;
""")


async def native_call(
    engine: AsyncEngine,
    payload: dict[str, object],
    *,
    wire_hash: str | None = None,
) -> object:
    from sqlalchemy import text

    encoded = canonical(payload)
    scope = payload["scope"]
    confirmation = payload["confirmation"]
    assert isinstance(scope, dict) and isinstance(confirmation, dict)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "SELECT set_config('app.tenant_id','00000000-0000-4000-8000-000000000000',true),"
                "set_config('app.edge_node_id','00000000-0000-4000-8000-000000000000',true),"
                "set_config('app.support_session','true',true)"
            )
        )
        return await connection.scalar(
            text(
                "SELECT public.dispatch_edge_cash_sale_v1(:operation,:activation,:epoch,:actor,"
                "CAST(:payload AS JSONB),:hash)"
            ),
            {
                "operation": UUID(str(confirmation["operation_id"])),
                "activation": UUID(str(scope["activation_id"])),
                "epoch": scope["writer_epoch"],
                "actor": UUID(str(scope["user_id"])),
                "payload": encoded,
                "hash": wire_hash or hashlib.sha256(encoded.encode()).hexdigest(),
            },
        )


async def denied(engine: AsyncEngine, sql: str) -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    try:
        async with engine.begin() as connection:
            await connection.execute(text(sql))
    except DBAPIError as error:
        assert getattr(error.orig, "sqlstate", None) == "42501"
    else:
        raise AssertionError("Native direct access unexpectedly succeeded")


async def check_replay(admin: Admin, fixture: Fixture, app_url: URL) -> None:
    from app.domains.pos.edge_retail_cash_replay import replay_payload
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(
        app_url.set(username=fixture.node_role, password=fixture.password),
        poolclass=NullPool,
    )
    payload = json.loads(replay_payload(fixture.command))
    before = admin.snapshot(fixture.command.scope.tenant_id)
    try:
        # An ordinary completed app marker is not an immutable native command anchor.
        assert await native_call(engine, payload) is None
        assert admin.snapshot(fixture.command.scope.tenant_id) == before
        provision_anchor(admin, fixture)
        before = admin.snapshot(fixture.command.scope.tenant_id)
        for _ in range(2):
            assert await native_call(engine, payload) == fixture.wrapper().model_dump(mode="json")
        mutations = (
            (
                "kind",
                None,
                ("mixed_cash" if fixture.command.kind == "physical_cash" else "physical_cash"),
            ),
            ("scope", "activation_id", str(uuid4())),
            ("scope", "writer_epoch", 3),
            ("scope", "user_id", str(uuid4())),
            ("scope", "tenant_id", str(uuid4())),
            ("scope", "branch_id", str(uuid4())),
            ("scope", "edge_node_id", str(uuid4())),
            ("scope", "register_id", str(uuid4())),
            (
                "confirmation",
                "cash_tendered",
                format(fixture.command.confirmation.cash_tendered + 1, ".2f"),
            ),
            ("confirmation", "expected_revision_hash", "0" * 64),
        )
        for field, nested, value in mutations:
            altered = json.loads(canonical(payload))
            if nested is None:
                altered[field] = value
            else:
                altered[field][nested] = value
            try:
                await native_call(engine, altered)
            except DBAPIError as error:
                assert getattr(error.orig, "sqlstate", None) in {"42501", "23505"}
            else:
                raise AssertionError("Native replay accepted a rebound command")
        try:
            await native_call(engine, payload, wire_hash="f" * 64)
        except DBAPIError as error:
            assert getattr(error.orig, "sqlstate", None) == "22023"
        else:
            raise AssertionError("Native replay accepted an invalid wire hash")
        missing = json.loads(canonical(payload))
        missing["confirmation"]["operation_id"] = str(uuid4())
        assert await native_call(engine, missing) is None
        await denied(engine, "SELECT * FROM public.pos_physical_checkout")
        await denied(engine, "SELECT * FROM public.pos_mixed_checkout")
        await denied(engine, "SELECT * FROM public.edge_cash_command")
        await denied(engine, "SET ROLE aurum_edge_cash_owner")
        await denied(
            engine,
            "SELECT public.replay_edge_retail_cash_v1(NULL,NULL,1,NULL,'{}'::JSONB,repeat('a',64))",
        )
        assert admin.snapshot(fixture.command.scope.tenant_id) == before
    finally:
        await engine.dispose()


async def check_corrupt_anchor(
    admin: Admin,
    support: AsyncEngine,
    maintenance: AsyncEngine,
    factory: async_sessionmaker[AsyncSession],
    app_url: URL,
    fixtures: list[Fixture],
    diagnostic: DiagnosticState,
) -> None:
    from app.domains.pos.edge_retail_cash_replay import replay_payload
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    # Separate immutable corrupt anchor proves the stored result hash is checked;
    # no completed Sale, marker or existing anchor is modified for this case.
    diagnostic.phase = "app_checkout_corrupt_anchor"
    corrupt = await completed_fixture(support, maintenance, factory, "physical_cash")
    diagnostic.phase = "synthetic_corrupt_anchor"
    provision_identity(admin, corrupt)
    fixtures.append(corrupt)
    provision_anchor(admin, corrupt, corrupt_hash=True)
    engine = create_async_engine(
        app_url.set(username=corrupt.node_role, password=corrupt.password),
        poolclass=NullPool,
    )
    try:
        diagnostic.phase = "native_corrupt_anchor"
        before = admin.snapshot(corrupt.command.scope.tenant_id)
        try:
            await native_call(engine, json.loads(replay_payload(corrupt.command)))
        except DBAPIError as error:
            assert getattr(error.orig, "sqlstate", None) == "23505"
        else:
            raise AssertionError("Native replay accepted a corrupt immutable result hash")
        assert admin.snapshot(corrupt.command.scope.tenant_id) == before
    finally:
        await engine.dispose()


async def main(diagnostic: DiagnosticState | None = None) -> None:
    require_ci_context()

    from app.core.config import get_settings
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    diagnostic = diagnostic or DiagnosticState()
    container = os.environ["POSTGRES_SERVICE_ID"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", container):
        raise RuntimeError("Invalid disposable PostgreSQL container identifier")
    settings = get_settings()
    app_url = ci_database_url(settings.DATABASE_URL_APP)
    support_url = ci_database_url(settings.DATABASE_URL_SUPPORT)
    migration_url = ci_database_url(os.environ["DATABASE_URL_MIGRATION"])
    if any(
        url.database != app_url.database
        or url.host != app_url.host
        or (url.port or 5432) != (app_url.port or 5432)
        for url in (support_url, migration_url)
    ):
        raise RuntimeError("Replay fixture engines must share one disposable database")
    assert app_url.database is not None
    admin = Admin(container, app_url.database)
    assert admin.run("SELECT current_database()") == app_url.database
    support = create_async_engine(support_url, poolclass=NullPool)
    maintenance = create_async_engine(
        migration_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"role": "aurum_schema_owner"}},
    )
    app = create_async_engine(app_url, poolclass=NullPool)
    factory = async_sessionmaker(app, expire_on_commit=False)
    fixtures: list[Fixture] = []
    try:
        for kind in ("physical_cash", "mixed_cash"):
            diagnostic.phase = "app_checkout_" + kind
            fixture = await completed_fixture(support, maintenance, factory, kind)
            diagnostic.phase = "synthetic_identity_" + kind
            provision_identity(admin, fixture)
            fixtures.append(fixture)
            diagnostic.phase = "native_replay_" + kind
            await check_replay(admin, fixture, app_url)
        await check_corrupt_anchor(
            admin, support, maintenance, factory, app_url, fixtures, diagnostic
        )
    finally:
        for fixture in fixtures:
            admin.run(f"ALTER ROLE {identifier(fixture.node_role)} NOLOGIN PASSWORD NULL;")
        await app.dispose()
        await maintenance.dispose()
        await support.dispose()
    print(
        "Native anchored retail cash replay checks passed "
        "(2 kinds, synthetic anchors, no execution)."
    )


if __name__ == "__main__":
    diagnostic_state = DiagnosticState()
    try:
        asyncio.run(main(diagnostic_state))
    except Exception as error:
        # Test fixtures contain fictional sales and generated credentials. Even on
        # failure return only a safe exception class, never SQL parameters/data.
        print(
            json.dumps(
                {
                    "native_replay_proof": "failed",
                    "phase": diagnostic_state.phase,
                    "error": type(error).__name__,
                    "sqlstate": getattr(error, "sqlstate", None)
                    or getattr(getattr(error, "orig", None), "sqlstate", None),
                }
            )
        )
        raise SystemExit(1) from None
