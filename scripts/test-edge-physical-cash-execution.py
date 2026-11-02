"""CI-only genuine native physical cash proof; financial history is never seeded.

Receiving, actor setup, activation and lease authority are explicit test fixtures.
The native LOGIN and monetary writes are real. Financial guards and producer
gates stay enabled; only trusted activation provisioning suspends sync metadata
triggers. This does not prove TPM/bootstrap authority or enable runtime admission.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import runpy
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from app.domains.inventory.unit_stock_v2 import SaleFormatV2
    from app.domains.pos.edge_retail_cash import EdgeRetailCashCommandV1
    from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
    from app.domains.sync.offline_auth_lease import VerifiedOfflineLeasePrincipalV1
    from app.domains.sync.schemas import OfflinePosCommand, SignedOfflineAuthLeaseV1
    from sqlalchemy.engine import URL
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


class AdminAPI(Protocol):
    database: str

    def run(self, sql: str) -> str: ...

    def snapshot(self, tenant: UUID) -> str: ...


# Reuse the already tested stdlib-only guard and bounded fixture transport.
# Loading this local helper does not import runtime modules or access a database.
_shared = runpy.run_path(str(Path(__file__).with_name("test-edge-retail-cash-replay.py")))
require_ci_context = cast(Callable[[], None], _shared["require_ci_context"])
literal = cast(Callable[[object], str], _shared["literal"])
identifier = cast(Callable[[str], str], _shared["identifier"])
admin_factory = cast(Callable[[str, str], AdminAPI], _shared["Admin"])


@dataclass
class Diagnostic:
    phase: str = "entry_guard"


@dataclass(frozen=True)
class Fixture:
    command: EdgeRetailCashCommandV1
    lot_id: UUID
    initial_units: int
    node_role: str
    password: str = field(repr=False)


@dataclass
class FixedVerifier:
    """Only the lease authority is synthetic; database identity is never faked."""

    principal: VerifiedOfflineLeasePrincipalV1
    fail_call: int | None = None
    calls: int = 0

    async def authorize(
        self, lease: SignedOfflineAuthLeaseV1, command: OfflinePosCommand
    ) -> VerifiedOfflineLeasePrincipalV1:
        from app.domains.sync.offline_auth import OfflineAuthContractError

        self.calls += 1
        if (
            command != "sale.cash.complete"
            or lease.claims.scope != self.principal.scope
            or lease.claims.lease_id != self.principal.lease_id
            or self.calls == self.fail_call
        ):
            raise OfflineAuthContractError("Synthetic CI lease authority refused the attempt")
        return self.principal


def lease_fixture(fixture: Fixture) -> tuple[SignedOfflineAuthLeaseV1, FixedVerifier]:
    from app.domains.sync.offline_auth_lease import VerifiedOfflineLeasePrincipalV1
    from tests.domains.sync.test_offline_auth_lease_pipeline_v1 import _claims, _signed

    claims = _claims().model_copy(update={"lease_id": uuid4(), "scope": fixture.command.scope})
    lease = _signed(claims)
    return lease, FixedVerifier(
        VerifiedOfflineLeasePrincipalV1(
            lease_id=lease.claims.lease_id,
            session_id=uuid4(),
            scope=fixture.command.scope,
            command="sale.cash.complete",
            expires_at=lease.claims.expires_at,
        )
    )


def snapshot(admin: AdminAPI, fixture: Fixture) -> str:
    tenant = literal(fixture.command.scope.tenant_id)
    tables = (
        "unit_stock_entry_v2",
        "unit_stock_operation_v2",
        "sync_physical_stock_source",
        "sync_physical_stock_event",
        "sync_physical_stock_anchor",
        "sync_physical_stock_projection",
        "sync_physical_stock_baseline",
        "sync_sale_projection",
    )
    rows = " UNION ALL ".join(
        f"SELECT {literal(table)} AS kind,to_jsonb(row) AS body FROM public.{table} row "
        f"WHERE tenant_id={tenant}::UUID"
        for table in tables
    )
    extra = admin.run(
        "WITH rows AS (" + rows + ") SELECT md5(coalesce(jsonb_agg("
        "jsonb_build_array(kind,body) ORDER BY kind,body::TEXT)::TEXT,'[]')) FROM rows"
    )
    return admin.snapshot(fixture.command.scope.tenant_id) + ":" + extra


async def draft_fixture(
    support: AsyncEngine,
    maintenance: AsyncEngine,
    app_factory: async_sessionmaker[AsyncSession],
    sale_format: SaleFormatV2,
) -> Fixture:
    from app.domains.pos.edge_retail_cash import EdgeRetailCashCommandV1
    from app.domains.sync.schemas import OfflineAuthScopeV0
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession
    from tests.domains.incoming.test_unit_acceptance import (
        ReceivingRuntime,
        seed_receiving_actor,
        set_receiving_context,
    )
    from tests.domains.inventory.test_unit_ledger import seed_ledger_context
    from tests.domains.pos.test_retail_checkout_event_producer_integration import enable_gate
    from tests.domains.pos.test_unit_physical_cart import put_physical
    from tests.domains.pos.test_unit_physical_checkout_preparation import (
        checkout_source,
        saved_request,
    )
    from tests.domains.sync.test_physical_stock_events_integration import enable_stock_gate

    async with AsyncSession(support) as session, session.begin():
        await session.execute(text("SELECT set_config('app.support_session','true',true)"))
        stock = await seed_ledger_context(session)
    actor = await seed_receiving_actor(support, maintenance, stock)
    async with app_factory() as session:
        session.info["receiving_runtime"] = ReceivingRuntime(app_factory, stock, actor)
        await set_receiving_context(session, stock, actor)
        await enable_gate(session, maintenance)
        await enable_stock_gate(session, maintenance)
        source = await checkout_source(session)
        await put_physical(session, source.sale.id, sale_format=sale_format, quantity=1)
        request = await saved_request(session, source.sale)
        initial_units = int(
            await session.scalar(
                text(
                    "SELECT closed_packs*(packaging->>'units_per_pack')::BIGINT "
                    "+coalesce((SELECT sum(remaining_units) FROM public.unit_stock_opened_pack_v2 "
                    "WHERE lot_id=lot.id AND tenant_id=lot.tenant_id),0) "
                    "FROM public.unit_stock_lot_v2 lot WHERE id=:lot"
                ),
                {"lot": source.stock.lot_id},
            )
        )
        await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        await session.commit()
    scope = OfflineAuthScopeV0(
        activation_id=uuid4(),
        tenant_id=stock.tenant_id,
        branch_id=stock.branch_id,
        edge_node_id=uuid4(),
        register_id=source.sale.register_id,
        writer_epoch=2,
        user_id=actor,
        capability="cash_sale_v1",
    )
    return Fixture(
        EdgeRetailCashCommandV1(kind="physical_cash", scope=scope, confirmation=request),
        source.stock.lot_id,
        initial_units,
        "aurum_edge_node_" + scope.edge_node_id.hex,
        secrets.token_hex(32),
    )


def provision_active_identity(admin: AdminAPI, fixture: Fixture) -> None:
    scope = fixture.command.scope
    # Activation/TPM authority is synthetic. Actual financial tables, their
    # guards and both producer gates remain untouched and enabled throughout.
    admin.run(f"""
BEGIN;
CREATE TEMP TABLE native_execution_context ON COMMIT DROP AS
SELECT stream.*,coalesce(counter.last_receipt_seq,0) AS receipt_baseline
FROM public.sync_stream stream LEFT JOIN public.register_receipt_counter counter
 ON counter.tenant_id=stream.tenant_id AND counter.register_id={literal(scope.register_id)}
WHERE stream.tenant_id={literal(scope.tenant_id)} AND stream.branch_id={literal(scope.branch_id)};
ALTER TABLE public.sync_node DISABLE TRIGGER USER;
ALTER TABLE public.sync_writer_activation DISABLE TRIGGER USER;
ALTER TABLE public.sync_writer_epoch DISABLE TRIGGER USER;
ALTER TABLE public.sync_stream DISABLE TRIGGER USER;
INSERT INTO public.sync_node(
 id,tenant_id,branch_id,register_id,node_kind,mode,status,display_name,
 credential_kid,credential_hash,credential_issued_at,credential_expires_at,
 shadow_start_origin_node_id,shadow_start_writer_epoch,shadow_start_sequence,
 shadow_start_checksum,shadow_start_projection_checksum)
SELECT {literal(scope.edge_node_id)},tenant_id,branch_id,{literal(scope.register_id)},
 'edge','edge_writer','active','Synthetic activation; real native execution',
 gen_random_uuid(),encode(gen_random_bytes(32),'hex'),now(),now()+interval '1 day',
 writer_node_id,writer_epoch,last_sequence,current_checksum,current_projection_checksum
FROM native_execution_context;
ALTER TABLE native_execution_context ADD COLUMN manifest TEXT,
 ADD COLUMN source_root TEXT,ADD COLUMN projection_root TEXT;
UPDATE native_execution_context SET manifest=encode(sha256(convert_to(
 'aurum:handover-manifest:v1:'||{literal(scope.activation_id)}||':'||tenant_id::TEXT||':'
 ||branch_id::TEXT||':'||{literal(scope.edge_node_id)}||':'||{literal(scope.register_id)}
 ||':2:'||writer_epoch::TEXT||':'||last_sequence::TEXT||':'||current_checksum||':'
 ||current_projection_checksum||':'||repeat('e',64)||':'||receipt_baseline::TEXT,'UTF8')),'hex');
UPDATE native_execution_context SET source_root=encode(sha256(convert_to(
 'aurum:writer-root:source:v1:'||current_checksum||':'||repeat('e',64)||':'||manifest,'UTF8')),'hex'),
 projection_root=encode(sha256(convert_to('aurum:writer-root:projection:v1:'
 ||current_projection_checksum||':'||repeat('e',64)||':'||manifest,'UTF8')),'hex');
UPDATE public.sync_writer_epoch SET state='fenced',fenced_at=statement_timestamp()
WHERE tenant_id={literal(scope.tenant_id)} AND branch_id={literal(scope.branch_id)}
 AND writer_epoch=1 AND state='active';
INSERT INTO public.sync_writer_activation(
 activation_id,tenant_id,branch_id,writer_epoch,writer_node_id,allowed_register_id,
 capability,state,root_source_checksum,root_projection_checksum,last_sequence,
 current_source_checksum,current_projection_checksum,previous_writer_epoch,
 previous_terminal_sequence,previous_terminal_source_checksum,
 previous_terminal_projection_checksum,bootstrap_snapshot_hash,activation_manifest_hash,
 receipt_baseline_seq,prepare_request_hash,prepared_at,ready_at,activated_at)
SELECT {literal(scope.activation_id)},tenant_id,branch_id,2,{literal(scope.edge_node_id)},
 {literal(scope.register_id)},'cash_sale_v1','activated',source_root,projection_root,0,
 source_root,projection_root,writer_epoch,last_sequence,current_checksum,
 current_projection_checksum,repeat('e',64),manifest,receipt_baseline,repeat('d',64),now(),now(),now()
FROM native_execution_context;
INSERT INTO public.sync_writer_epoch(
 activation_id,tenant_id,branch_id,writer_epoch,writer_node_id,allowed_register_id,
 capability,state,root_source_checksum,root_projection_checksum,last_sequence,
 current_source_checksum,current_projection_checksum,previous_writer_epoch,
 previous_terminal_sequence,previous_terminal_source_checksum,
 previous_terminal_projection_checksum,bootstrap_snapshot_hash,activation_manifest_hash,
 receipt_baseline_seq,prepared_at,activated_at)
SELECT {literal(scope.activation_id)},tenant_id,branch_id,2,{literal(scope.edge_node_id)},
 {literal(scope.register_id)},'cash_sale_v1','active',source_root,projection_root,0,
 source_root,projection_root,writer_epoch,last_sequence,current_checksum,
 current_projection_checksum,repeat('e',64),manifest,receipt_baseline,now(),now()
FROM native_execution_context;
UPDATE public.sync_stream stream SET writer_node_id={literal(scope.edge_node_id)},
 writer_epoch=2,last_sequence=0,current_checksum=context.source_root,
 current_projection_checksum=context.projection_root FROM native_execution_context context
WHERE stream.id=context.id;
INSERT INTO public.register_receipt_counter(
 tenant_id,branch_id,register_id,writer_epoch,last_receipt_seq)
VALUES({literal(scope.tenant_id)},{literal(scope.branch_id)},{literal(scope.register_id)},2,0)
ON CONFLICT(tenant_id,register_id) DO UPDATE SET writer_epoch=2;
SET CONSTRAINTS ALL IMMEDIATE;
ALTER TABLE public.sync_stream ENABLE TRIGGER USER;
ALTER TABLE public.sync_writer_epoch ENABLE TRIGGER USER;
ALTER TABLE public.sync_writer_activation ENABLE TRIGGER USER;
ALTER TABLE public.sync_node ENABLE TRIGGER USER;
CREATE ROLE {identifier(fixture.node_role)} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
 NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 1 PASSWORD {literal(fixture.password)};
GRANT aurum_edge_cash_executor TO {identifier(fixture.node_role)}
 WITH ADMIN FALSE,INHERIT TRUE,SET FALSE;
GRANT CONNECT ON DATABASE {identifier(admin.database)} TO {identifier(fixture.node_role)};
INSERT INTO public.edge_cash_node_identity(
 tenant_id,branch_id,edge_node_id,register_id,database_role,database_role_oid)
SELECT {literal(scope.tenant_id)},{literal(scope.branch_id)},{literal(scope.edge_node_id)},
 {literal(scope.register_id)},{literal(fixture.node_role)},oid FROM pg_catalog.pg_roles
WHERE rolname={literal(fixture.node_role)};
COMMIT;
""")


async def complete(
    native_factory: async_sessionmaker[AsyncSession],
    fixture: Fixture,
    *,
    fail_call: int | None = None,
) -> PhysicalCheckoutResultV3:
    from app.domains.pos.edge_physical_cash_execution import NativeEdgePhysicalCashExecutionV1
    from app.domains.pos.edge_retail_cash import EdgeRetailCashKernelV1
    from app.domains.pos.physical_documents import PhysicalCheckoutResultV3

    lease, verifier = lease_fixture(fixture)
    verifier.fail_call = fail_call
    result = await EdgeRetailCashKernelV1(
        verifier=verifier, execution=NativeEdgePhysicalCashExecutionV1(native_factory)
    ).complete(lease=lease, command=fixture.command)
    assert type(result) is PhysicalCheckoutResultV3
    return result


def assert_posted(admin: AdminAPI, fixture: Fixture, result: PhysicalCheckoutResultV3) -> None:
    scope, operation = fixture.command.scope, fixture.command.confirmation.operation_id
    raw = admin.run(f"""
SELECT jsonb_build_object(
 'sales',(SELECT count(*) FROM public.sale WHERE tenant_id={literal(scope.tenant_id)}
   AND id={literal(result.sale_id)} AND status='completed' AND operation_id={literal(operation)}),
 'markers',(SELECT count(*) FROM public.pos_physical_checkout
   WHERE tenant_id={literal(scope.tenant_id)} AND id={literal(operation)}),
 'payments',(SELECT count(*) FROM public.sale_payment WHERE sale_id={literal(result.sale_id)}
   AND id={literal(operation)} AND amount={literal(result.total_amount)} AND currency='TJS'),
 'components',(SELECT count(*) FROM public.pos_unit_checkout_payment_component
   WHERE tenant_id={literal(scope.tenant_id)} AND physical_checkout_id={literal(operation)}),
 'anchors',(SELECT count(*) FROM public.edge_cash_command
   WHERE tenant_id={literal(scope.tenant_id)} AND operation_id={literal(operation)}
   AND edge_node_id={literal(scope.edge_node_id)} AND writer_epoch=2),
 'origins',(SELECT count(*) FROM public.pos_retail_checkout_origin
   WHERE tenant_id={literal(scope.tenant_id)} AND id={literal(operation)}
   AND origin_node_id={literal(scope.edge_node_id)} AND writer_epoch=2),
 'retail_events',(SELECT count(*) FROM public.pos_retail_checkout_event
   WHERE tenant_id={literal(scope.tenant_id)} AND id={literal(operation)}),
 'stock_events',(SELECT count(*) FROM public.sync_physical_stock_event
   WHERE tenant_id={literal(scope.tenant_id)} AND origin_node_id={literal(scope.edge_node_id)}
   AND writer_epoch=2 AND envelope->'payload'->>'source_id'={literal(operation)}),
 'outbox',(SELECT count(*) FROM public.sync_outbox WHERE tenant_id={literal(scope.tenant_id)}
   AND origin_node_id={literal(scope.edge_node_id)} AND writer_epoch=2),
 'sequence',(SELECT last_sequence FROM public.sync_stream
   WHERE tenant_id={literal(scope.tenant_id)} AND branch_id={literal(scope.branch_id)}),
 'remaining_units',(SELECT closed_packs*(packaging->>'units_per_pack')::BIGINT
   +coalesce((SELECT sum(remaining_units) FROM public.unit_stock_opened_pack_v2
    WHERE lot_id=lot.id AND tenant_id=lot.tenant_id),0)
   FROM public.unit_stock_lot_v2 lot WHERE id={literal(fixture.lot_id)}));
""")
    facts = json.loads(raw)
    assert facts == {
        "sales": 1,
        "markers": 1,
        "payments": 1,
        "components": 1,
        "anchors": 1,
        "origins": 1,
        "retail_events": 1,
        "stock_events": 1,
        "outbox": 2,
        "sequence": 2,
        "remaining_units": fixture.initial_units - sum(line.base_units for line in result.lines),
    }


def close_shift_fixture(
    admin: AdminAPI, fixture: Fixture, result: PhysicalCheckoutResultV3
) -> None:
    # This explicit maintenance fixture only establishes historical shift state.
    # It does not test cashier close-shift authorization or alter a completed sale.
    admin.run(f"""
BEGIN;
SELECT set_config('app.user_id',{literal(fixture.command.scope.user_id)},true) AS actor \\gset
UPDATE public.shift SET status='closed',closed_at=statement_timestamp(),
 closed_by_user_id={literal(fixture.command.scope.user_id)},
 closing_cash_actual={literal(result.total_amount)},closing_cash_expected={literal(result.total_amount)},
 closing_difference=0 WHERE id={literal(result.shift_id)};
COMMIT;
""")


async def assert_native_denials(engine: AsyncEngine) -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    for sql in (
        "SELECT * FROM public.unit_stock_lot_v2",
        "SET ROLE aurum_app",
        "SET ROLE aurum_schema_owner",
        "SELECT * FROM public.edge_cash_command",
    ):
        try:
            async with engine.begin() as connection:
                await connection.execute(text(sql))
        except DBAPIError as error:
            assert getattr(error.orig, "sqlstate", None) == "42501"
        else:
            raise AssertionError("Native direct access unexpectedly succeeded")


async def assert_envelope_denials(
    factory: async_sessionmaker[AsyncSession],
    admin: AdminAPI,
    fixture: Fixture,
) -> None:
    from app.domains.pos.edge_physical_cash_execution import physical_completion_payload
    from app.domains.pos.edge_physical_cash_plan import build_edge_physical_cash_plan
    from app.domains.pos.edge_retail_cash_draft import EdgeRetailCashDraftRepository
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    async with factory() as session, session.begin():
        draft = await EdgeRetailCashDraftRepository(session).lock_draft(fixture.command)
        wire = json.loads(
            physical_completion_payload(fixture.command, build_edge_physical_cash_plan(draft))
        )
    before = snapshot(admin, fixture)
    for mutation in ("wire_hash", "stock_hash", "units", "lot", "scope", "mixed"):
        payload = copy.deepcopy(wire)
        if mutation == "stock_hash":
            payload["plan"]["stock_request_hash"] = "0" * 64
        elif mutation == "units":
            payload["plan"]["movements"][0]["units_taken"] += 1
        elif mutation == "lot":
            payload["plan"]["movements"][0]["lot_id"] = str(uuid4())
        elif mutation == "scope":
            payload["scope"]["tenant_id"] = str(uuid4())
        elif mutation == "mixed":
            payload["kind"] = "mixed_cash"
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        request_hash = (
            "0" * 64
            if mutation == "wire_hash"
            else hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        )
        try:
            async with factory() as session, session.begin():
                # Caller-provided tenancy/support GUCs cannot substitute for the
                # actual native LOGIN and command scope bound by PostgreSQL.
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id',:tenant,true),"
                        "set_config('app.support_session','true',true)"
                    ),
                    {"tenant": str(uuid4())},
                )
                await session.execute(
                    text(
                        "SELECT public.dispatch_edge_cash_sale_v1("
                        ":operation,:activation,:epoch,:actor,CAST(:payload AS jsonb),:hash)"
                    ),
                    {
                        "operation": fixture.command.confirmation.operation_id,
                        "activation": fixture.command.scope.activation_id,
                        "epoch": fixture.command.scope.writer_epoch,
                        "actor": fixture.command.scope.user_id,
                        "payload": encoded,
                        "hash": request_hash,
                    },
                )
        except DBAPIError as error:
            assert getattr(error.orig, "sqlstate", None) in {"22023", "23514", "42501"}
        else:
            raise AssertionError("Native SQL accepted a malicious completion envelope")
        assert snapshot(admin, fixture) == before


async def assert_transaction_denials(
    engine: AsyncEngine,
    admin: AdminAPI,
    fixture: Fixture,
) -> None:
    from app.domains.sync.offline_auth import OfflineAuthContractError
    from sqlalchemy.ext.asyncio import async_sessionmaker

    for isolation in ("AUTOCOMMIT", "REPEATABLE READ"):
        before = snapshot(admin, fixture)
        factory = async_sessionmaker(
            engine.execution_options(isolation_level=isolation), expire_on_commit=False
        )
        try:
            await complete(factory, fixture)
        except OfflineAuthContractError:
            pass
        else:
            raise AssertionError("Native cash accepted an unsafe transaction mode")
        assert snapshot(admin, fixture) == before


async def assert_write_fault_rollbacks(
    factory: async_sessionmaker[AsyncSession],
    admin: AdminAPI,
    fixture: Fixture,
) -> None:
    from sqlalchemy.exc import DBAPIError

    # Temporary triggers belong only to this guarded disposable CI database.
    # They inject failures; no financial guard is removed or weakened.
    for table, column, timing in (
        ("unit_stock_movement_v2", "command_id", "AFTER"),
        ("sale_payment", "id", "AFTER"),
        ("edge_cash_command", "operation_id", "BEFORE"),
    ):
        token = "native_cash_fault_" + uuid4().hex
        before = snapshot(admin, fixture)
        admin.run(f"""
CREATE FUNCTION public.{identifier(token)}() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $fault$
BEGIN
 IF NEW.tenant_id={literal(fixture.command.scope.tenant_id)}::UUID
  AND NEW.{column}={literal(fixture.command.confirmation.operation_id)}::UUID THEN
  RAISE EXCEPTION 'Disposable native execution rollback proof' USING ERRCODE='P0001';
 END IF;
 RETURN NEW;
END $fault$;
CREATE TRIGGER {identifier(token)} {timing} INSERT ON public.{table}
FOR EACH ROW EXECUTE FUNCTION public.{identifier(token)}();
""")
        try:
            try:
                await complete(factory, fixture)
            except DBAPIError as error:
                assert getattr(error.orig, "sqlstate", None) == "P0001"
            else:
                raise AssertionError("Native execution escaped an injected financial failure")
        finally:
            admin.run(
                f"DROP TRIGGER {identifier(token)} ON public.{table};"
                f"DROP FUNCTION public.{identifier(token)}();"
            )
        assert snapshot(admin, fixture) == before


async def assert_waiting_login_revocation(
    factory: async_sessionmaker[AsyncSession],
    maintenance: AsyncEngine,
    admin: AdminAPI,
    fixture: Fixture,
) -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    before = snapshot(admin, fixture)
    task: asyncio.Task[PhysicalCheckoutResultV3] | None = None
    try:
        async with maintenance.begin() as owner:
            await owner.execute(
                text("SELECT id FROM public.unit_stock_lot_v2 WHERE id=:lot FOR UPDATE"),
                {"lot": fixture.lot_id},
            )
            task = asyncio.create_task(complete(factory, fixture))
            deadline = asyncio.get_running_loop().time() + 15
            while (
                admin.run(
                    "SELECT count(*) FROM pg_catalog.pg_stat_activity "
                    f"WHERE usename={literal(fixture.node_role)} AND wait_event_type='Lock'"
                )
                != "1"
            ):
                if task.done() or asyncio.get_running_loop().time() >= deadline:
                    raise AssertionError("Native request did not wait for the held stock lock")
                await asyncio.sleep(0.05)
            # Revocation is a real catalog change, while the original native
            # connection remains alive. The next dispatcher must recheck it.
            admin.run(f"ALTER ROLE {identifier(fixture.node_role)} NOLOGIN;")
        assert task is not None
        try:
            await asyncio.wait_for(task, timeout=15)
        except DBAPIError as error:
            assert getattr(error.orig, "sqlstate", None) == "42501"
        else:
            raise AssertionError("Native execution ignored LOGIN revocation during its wait")
        assert snapshot(admin, fixture) == before
    finally:
        admin.run(f"ALTER ROLE {identifier(fixture.node_role)} LOGIN;")
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def check_fixture(
    admin: AdminAPI,
    fixture: Fixture,
    app_url: URL,
    diagnostic: Diagnostic,
    maintenance: AsyncEngine,
) -> None:
    from app.domains.sync.offline_auth import OfflineAuthContractError
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    # The production node role is restricted to one connection. Concurrent
    # requests queue in this one-connection pool rather than loosening that role.
    engine = create_async_engine(
        app_url.set(username=fixture.node_role, password=fixture.password),
        pool_size=1,
        max_overflow=0,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        diagnostic.phase = "native_transaction_modes"
        await assert_transaction_denials(engine, admin, fixture)
        diagnostic.phase = "native_malicious_envelopes"
        await assert_envelope_denials(factory, admin, fixture)
        diagnostic.phase = "native_write_fault_rollbacks"
        await assert_write_fault_rollbacks(factory, admin, fixture)
        diagnostic.phase = "native_waiting_login_revocation"
        await assert_waiting_login_revocation(factory, maintenance, admin, fixture)
        diagnostic.phase = "late_authority_rollback"
        before = snapshot(admin, fixture)
        try:
            await complete(factory, fixture, fail_call=3)
        except OfflineAuthContractError:
            pass
        else:
            raise AssertionError("Native cash ignored final authorization failure")
        assert snapshot(admin, fixture) == before
        diagnostic.phase = "native_concurrent_cash_completion"
        first, second = await asyncio.gather(complete(factory, fixture), complete(factory, fixture))
        assert first == second
        assert_posted(admin, fixture, first)
        before = snapshot(admin, fixture)
        assert await complete(factory, fixture) == first
        assert snapshot(admin, fixture) == before
        diagnostic.phase = "native_direct_access_denials"
        await assert_native_denials(engine)
        assert snapshot(admin, fixture) == before
        close_shift_fixture(admin, fixture, first)
        diagnostic.phase = "native_closed_shift_replay"
        before = snapshot(admin, fixture)
        assert await complete(factory, fixture) == first
        assert snapshot(admin, fixture) == before
    finally:
        await engine.dispose()


async def main(diagnostic: Diagnostic | None = None) -> None:
    require_ci_context()

    from app.core.config import get_settings
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    diagnostic = diagnostic or Diagnostic()
    settings = get_settings()
    parse_url = cast(Callable[[str], URL], _shared["ci_database_url"])
    app_url = parse_url(settings.DATABASE_URL_APP)
    support_url = parse_url(settings.DATABASE_URL_SUPPORT)
    migration_url = parse_url(os.environ["DATABASE_URL_MIGRATION"])
    assert app_url.database is not None
    admin = admin_factory(os.environ["POSTGRES_SERVICE_ID"], app_url.database)
    assert admin.run("SELECT current_database()") == app_url.database
    app = create_async_engine(app_url, poolclass=NullPool)
    support = create_async_engine(support_url, poolclass=NullPool)
    maintenance = create_async_engine(
        migration_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"role": "aurum_schema_owner"}},
    )
    factory = async_sessionmaker(app, expire_on_commit=False)
    fixtures: list[Fixture] = []
    try:
        for sale_format in ("pack", "blister", "unit"):
            diagnostic.phase = "app_draft_" + sale_format
            fixture = await draft_fixture(support, maintenance, factory, sale_format)
            diagnostic.phase = "synthetic_activation_" + sale_format
            provision_active_identity(admin, fixture)
            fixtures.append(fixture)
            await check_fixture(admin, fixture, app_url, diagnostic, maintenance)
        diagnostic.phase = "genuine_history_blocks_downgrade"
        from tests.isolation.test_edge_physical_cash_execution_migration import (
            assert_native_history_blocks_downgrade,
        )

        async with maintenance.connect() as connection, connection.begin():
            await connection.run_sync(assert_native_history_blocks_downgrade)
    finally:
        for fixture in fixtures:
            admin.run(f"ALTER ROLE {identifier(fixture.node_role)} NOLOGIN PASSWORD NULL;")
        await app.dispose()
        await support.dispose()
        await maintenance.dispose()
    print(
        "Native physical cash execution checks passed "
        "(3 formats, genuine monetary anchors, rollback, replay, gates enabled)."
    )


if __name__ == "__main__":
    diagnostic_state = Diagnostic()
    try:
        asyncio.run(main(diagnostic_state))
    except Exception as error:
        print(
            json.dumps(
                {
                    "native_physical_execution_proof": "failed",
                    "phase": diagnostic_state.phase,
                    "error": type(error).__name__,
                    "sqlstate": getattr(error, "sqlstate", None)
                    or getattr(getattr(error, "orig", None), "sqlstate", None),
                }
            )
        )
        raise SystemExit(1) from None
