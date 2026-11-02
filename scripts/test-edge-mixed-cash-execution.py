"""CI-only mixed monetary proof under actual native LOGIN and enabled guards.

Only receiving, actor/activation setup and lease authority are synthetic fixtures.
Completed sales, payments, origins, events and native anchors are never seeded.
Physical v5 owns physical stock projection; retail v4 never reapplies that stock.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import runpy
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from app.domains.inventory.unit_stock_v2 import SaleFormatV2
    from app.domains.pos.edge_retail_cash import EdgeRetailCashCommandV1, EdgeRetailCashKind
    from app.domains.pos.mixed_documents import MixedCheckoutResultV4
    from app.domains.pos.models import Sale
    from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
    from app.domains.sync.offline_auth_lease import VerifiedOfflineLeasePrincipalV1
    from app.domains.sync.schemas import OfflinePosCommand, SignedOfflineAuthLeaseV1
    from sqlalchemy.engine import URL
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


type CashResult = PhysicalCheckoutResultV3 | MixedCheckoutResultV4
type SaleConfigurer = Callable[[AsyncSession, Sale], Awaitable[None]]
type FaultPoint = tuple[str, str, str, UUID]


class AdminAPI(Protocol):
    database: str

    def run(self, sql: str) -> str: ...

    def snapshot(self, tenant: UUID) -> str: ...


class NativeCashFixture(Protocol):
    @property
    def command(self) -> EdgeRetailCashCommandV1: ...

    @property
    def lot_id(self) -> UUID: ...

    @property
    def initial_units(self) -> int: ...

    @property
    def node_role(self) -> str: ...

    @property
    def password(self) -> str: ...

    @property
    def existing_pack_id(self) -> UUID | None: ...


class LeaseVerifier(Protocol):
    fail_call: int | None

    async def authorize(
        self,
        lease: SignedOfflineAuthLeaseV1,
        command: OfflinePosCommand,
    ) -> VerifiedOfflineLeasePrincipalV1: ...


class CashCompleter(Protocol):
    async def __call__(
        self,
        native_factory: async_sessionmaker[AsyncSession],
        fixture: NativeCashFixture,
        *,
        fail_call: int | None = None,
    ) -> CashResult: ...


class DraftFactory(Protocol):
    async def __call__(
        self,
        support: AsyncEngine,
        maintenance: AsyncEngine,
        app_factory: async_sessionmaker[AsyncSession],
        lines: tuple[tuple[SaleFormatV2, int], ...],
        *,
        existing_fragment: bool = False,
        configure_sale: SaleConfigurer | None = None,
        kind: EdgeRetailCashKind = "physical_cash",
    ) -> NativeCashFixture: ...


class TransactionProof(Protocol):
    async def __call__(
        self,
        engine: AsyncEngine,
        admin: AdminAPI,
        fixture: NativeCashFixture,
        *,
        complete_call: CashCompleter,
    ) -> None: ...


class FaultProof(Protocol):
    async def __call__(
        self,
        factory: async_sessionmaker[AsyncSession],
        admin: AdminAPI,
        fixture: NativeCashFixture,
        *,
        complete_call: CashCompleter,
        fault_points: tuple[FaultPoint, ...],
    ) -> None: ...


class WaitingProof(Protocol):
    async def __call__(
        self,
        factory: async_sessionmaker[AsyncSession],
        maintenance: AsyncEngine,
        admin: AdminAPI,
        fixture: NativeCashFixture,
        *,
        complete_call: CashCompleter,
    ) -> None: ...


class DenialProof(Protocol):
    async def __call__(
        self,
        engine: AsyncEngine,
        *,
        extra_statements: tuple[str, ...] = (),
    ) -> None: ...


type Snapshot = Callable[[AdminAPI, NativeCashFixture], str]
type LeaseFactory = Callable[[NativeCashFixture], tuple[SignedOfflineAuthLeaseV1, LeaseVerifier]]
type AppCloser = Callable[
    [async_sessionmaker[AsyncSession], NativeCashFixture, CashResult], Awaitable[None]
]
type AllocatorProof = Callable[[AdminAPI, NativeCashFixture, CashResult], None]

# Import only the stdlib-safe sibling definitions. Reuse is explicit through
# callback interfaces; its globals and its five physical cases are never patched.
_physical = runpy.run_path(str(Path(__file__).with_name("test-edge-physical-cash-execution.py")))
require_ci_context = cast(Callable[[], None], _physical["require_ci_context"])
literal = cast(Callable[[object], str], _physical["literal"])
identifier = cast(Callable[[str], str], _physical["identifier"])
admin_factory = cast(Callable[[str, str], AdminAPI], _physical["admin_factory"])
snapshot = cast(Snapshot, _physical["snapshot"])
lease_fixture = cast(LeaseFactory, _physical["lease_fixture"])
draft_factory = cast(DraftFactory, _physical["draft_fixture"])
provision_identity = cast(
    Callable[[AdminAPI, NativeCashFixture], None], _physical["provision_active_identity"]
)
transaction_proof = cast(TransactionProof, _physical["assert_transaction_denials"])
fault_proof = cast(FaultProof, _physical["assert_write_fault_rollbacks"])
waiting_proof = cast(WaitingProof, _physical["assert_waiting_login_revocation"])
denial_proof = cast(DenialProof, _physical["assert_native_denials"])
app_close = cast(AppCloser, _physical["close_shift_fixture"])
allocator_proof = cast(AllocatorProof, _physical["assert_allocator_state"])
database_diagnostic = cast(
    Callable[[Exception], dict[str, str | None]], _physical["database_diagnostic"]
)
failure_site = cast(Callable[[Exception], str | None], _physical["failure_site"])


@dataclass
class Diagnostic:
    phase: str = "entry_guard"


async def configure_legacy(session: AsyncSession, sale: Sale, *, aggregate: bool) -> None:
    from app.domains.inventory.repository import InventoryRepository
    from tests.domains.pos.test_mixed_physical_checkout_preparation import (
        legacy_with_stock,
        second_legacy,
    )

    first = await legacy_with_stock(session, sale)
    if aggregate:
        await second_legacy(session, sale, first)
        await InventoryRepository(session).insert_movement(
            tenant_id=sale.tenant_id,
            batch_id=first.batch_id,
            movement_type="incoming",
            qty_delta=Decimal("0.001"),
        )


def assert_unposted_draft(
    admin: AdminAPI,
    fixture: NativeCashFixture,
    *,
    expected_legacy_rows: int,
) -> None:
    sale, operation = (
        fixture.command.confirmation.sale_id,
        fixture.command.confirmation.operation_id,
    )
    raw = admin.run(f"""
SELECT jsonb_build_object(
 'draft',(SELECT count(*) FROM public.sale WHERE id={literal(sale)} AND status='draft'),
 'legacy',(SELECT count(*) FROM public.sale_item WHERE sale_id={literal(sale)}
   AND qty=0.001 AND unit_price=25.00 AND total_price=0.02),
 'stocked',(SELECT bool_and(batch.qty_remaining=expected.quantity)
   FROM public.batch batch JOIN (SELECT batch_id,sum(qty) AS quantity
    FROM public.sale_item WHERE sale_id={literal(sale)} GROUP BY batch_id) expected
    ON expected.batch_id=batch.id),
 'payments',(SELECT count(*) FROM public.sale_payment WHERE sale_id={literal(sale)}),
 'markers',(SELECT count(*) FROM public.pos_mixed_checkout WHERE sale_id={literal(sale)}),
 'anchors',(SELECT count(*) FROM public.edge_cash_command WHERE operation_id={literal(operation)}),
 'commands',(SELECT count(*) FROM public.unit_stock_cart_command_v2 WHERE id={literal(operation)}));
""")
    facts = json.loads(raw)
    assert facts == {
        "draft": 1,
        "legacy": expected_legacy_rows,
        "stocked": True,
        "payments": 0,
        "markers": 0,
        "anchors": 0,
        "commands": 0,
    }


async def complete(
    native_factory: async_sessionmaker[AsyncSession],
    fixture: NativeCashFixture,
    *,
    fail_call: int | None = None,
) -> MixedCheckoutResultV4:
    from app.domains.pos.edge_mixed_cash_execution import NativeEdgeMixedCashExecutionV1
    from app.domains.pos.edge_retail_cash import EdgeRetailCashKernelV1
    from app.domains.pos.mixed_documents import MixedCheckoutResultV4

    lease, verifier = lease_fixture(fixture)
    verifier.fail_call = fail_call
    result = await EdgeRetailCashKernelV1(
        verifier=verifier,
        execution=NativeEdgeMixedCashExecutionV1(native_factory),
    ).complete(lease=lease, command=fixture.command)
    assert type(result) is MixedCheckoutResultV4
    return result


def assert_posted(
    admin: AdminAPI, fixture: NativeCashFixture, result: MixedCheckoutResultV4
) -> None:
    from app.domains.pos.mixed_documents import LegacyResultLineV4, MixedReceiptDocumentV4
    from app.domains.pos.physical_documents import PhysicalResultLineV3

    scope, operation = fixture.command.scope, fixture.command.confirmation.operation_id
    legacy = tuple(line for line in result.lines if isinstance(line, LegacyResultLineV4))
    physical = tuple(line for line in result.lines if isinstance(line, PhysicalResultLineV3))
    expected: dict[UUID, Decimal] = {}
    for line in legacy:
        assert line.quantity == Decimal("0.001") and line.gross_amount == Decimal("0.02")
        expected[line.batch_id] = expected.get(line.batch_id, Decimal("0.000")) + line.quantity
    assert legacy and physical and len(expected) == 1
    assert result.operation_id == result.stock_command_id == operation
    assert len(result.payments) == 1 and result.payments[0].method == "cash"
    assert (
        result.payments[0].amount
        == result.total_amount
        == fixture.command.confirmation.expected_total_amount
    )
    raw = admin.run(f"""
SELECT jsonb_build_object(
 'sales',(SELECT count(*) FROM public.sale WHERE id={literal(result.sale_id)}
   AND status='completed' AND operation_id={literal(operation)}),
 'markers',(SELECT count(*) FROM public.pos_mixed_checkout WHERE id={literal(operation)}),
 'physical_markers',(SELECT count(*) FROM public.pos_physical_checkout
   WHERE sale_id={literal(result.sale_id)}),
 'payments',(SELECT count(*) FROM public.sale_payment WHERE sale_id={literal(result.sale_id)}
   AND id={literal(operation)} AND amount={literal(result.total_amount)} AND currency='TJS'),
 'components',(SELECT count(*) FROM public.pos_unit_checkout_payment_component
   WHERE mixed_checkout_id={literal(operation)} AND physical_checkout_id IS NULL),
 'commands',(SELECT count(*) FROM public.unit_stock_cart_command_v2
   WHERE id={literal(operation)} AND mixed_checkout_sale_id={literal(result.sale_id)}
    AND checkout_sale_id IS NULL),
 'anchors',(SELECT count(*) FROM public.edge_cash_command WHERE operation_id={literal(operation)}
   AND edge_node_id={literal(scope.edge_node_id)} AND writer_epoch=2),
 'origins',(SELECT count(*) FROM public.pos_retail_checkout_origin WHERE id={literal(operation)}
   AND source_kind='mixed' AND origin_node_id={literal(scope.edge_node_id)} AND writer_epoch=2),
 'retail_events',(SELECT count(*) FROM public.pos_retail_checkout_event
   WHERE id={literal(operation)}),
 'stock_events',(SELECT count(*) FROM public.sync_physical_stock_event
   WHERE origin_node_id={literal(scope.edge_node_id)} AND writer_epoch=2
    AND envelope->'payload'->>'source_id'={literal(operation)}
    AND envelope->'payload'->>'source_owner'='checkout'
    AND envelope->'payload'->>'checkout_sale_id'={literal(result.sale_id)}
    AND envelope->'payload'->>'checkout_event_id'={literal(result.event_id)}),
 'outbox_types',(SELECT jsonb_agg(event_type ORDER BY event_type) FROM public.sync_outbox
   WHERE origin_node_id={literal(scope.edge_node_id)} AND writer_epoch=2),
 'sequence',(SELECT last_sequence FROM public.sync_stream
   WHERE tenant_id={literal(scope.tenant_id)} AND branch_id={literal(scope.branch_id)}),
 'remaining_units',(SELECT closed_packs*(packaging->>'units_per_pack')::BIGINT
   +coalesce((SELECT sum(remaining_units) FROM public.unit_stock_opened_pack_v2
    WHERE lot_id=lot.id AND tenant_id=lot.tenant_id),0)
   FROM public.unit_stock_lot_v2 lot WHERE id={literal(fixture.lot_id)}),
 'legacy_movements',(SELECT jsonb_agg(jsonb_build_object('batch',batch_id,
   'quantity',qty_delta::TEXT,'key',operation_key) ORDER BY batch_id)
   FROM public.batch_movement WHERE tenant_id={literal(scope.tenant_id)}
    AND source_table='sale' AND source_id={literal(result.sale_id)} AND movement_type='sale'),
 'legacy_remaining',(SELECT jsonb_agg(qty_remaining::TEXT ORDER BY id) FROM public.batch
   WHERE id IN (SELECT batch_id FROM public.sale_item WHERE sale_id={literal(result.sale_id)})),
 'receipt',(SELECT receipt_snapshot FROM public.sale WHERE id={literal(result.sale_id)}));
""")
    facts = json.loads(raw)
    receipt = MixedReceiptDocumentV4.model_validate(facts.pop("receipt"))
    assert receipt.total_amount == result.total_amount and receipt.is_test is False
    assert [(line.inventory_kind, line.quantity) for line in receipt.lines] == [
        (line.inventory_kind, line.quantity) for line in result.lines
    ]
    assert facts == {
        "sales": 1,
        "markers": 1,
        "physical_markers": 0,
        "payments": 1,
        "components": 1,
        "commands": 1,
        "anchors": 1,
        "origins": 1,
        "retail_events": 1,
        "stock_events": 1,
        "outbox_types": ["inventory.physical.changed.v5", "pos.sale.completed.v4"],
        "sequence": 2,
        "remaining_units": fixture.initial_units - sum(line.base_units for line in physical),
        "legacy_movements": [
            {
                "batch": str(batch),
                "quantity": format(-quantity, ".3f"),
                "key": f"pos:sale:{result.sale_id}:sale:{batch}",
            }
            for batch, quantity in sorted(expected.items())
        ],
        "legacy_remaining": ["0.000"] * len(expected),
    }
    if len(physical) == 3 or fixture.existing_pack_id is not None:
        allocator_proof(admin, fixture, result)


async def assert_envelope_denials(
    factory: async_sessionmaker[AsyncSession],
    admin: AdminAPI,
    fixture: NativeCashFixture,
) -> None:
    from app.domains.pos.edge_mixed_cash_execution import mixed_completion_payload
    from app.domains.pos.edge_mixed_cash_plan import build_edge_mixed_cash_plan
    from app.domains.pos.edge_retail_cash_draft import EdgeRetailCashDraftRepository
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    async with factory() as session, session.begin():
        draft = await EdgeRetailCashDraftRepository(session).lock_draft(fixture.command)
        wire = json.loads(
            mixed_completion_payload(fixture.command, build_edge_mixed_cash_plan(draft))
        )
    before = snapshot(admin, fixture)
    for mutation in (
        "wire_hash",
        "stock_hash",
        "legacy_batch",
        "legacy_quantity",
        "legacy_allocations",
        "movement_line",
        "scope",
        "kind",
    ):
        payload = copy.deepcopy(wire)
        if mutation == "stock_hash":
            payload["plan"]["stock_request_hash"] = "0" * 64
        elif mutation == "legacy_batch":
            payload["plan"]["quote"]["lines"][0]["batch_id"] = str(uuid4())
        elif mutation == "legacy_quantity":
            payload["plan"]["quote"]["lines"][0]["quantity"] = "0.002"
        elif mutation == "legacy_allocations":
            payload["plan"]["legacy_allocations"] = []
        elif mutation == "movement_line":
            payload["plan"]["movements"][-1]["line_id"] = payload["plan"]["quote"]["lines"][0]["id"]
        elif mutation == "scope":
            payload["scope"]["tenant_id"] = str(uuid4())
        elif mutation == "kind":
            payload["kind"] = "physical_cash"
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        request_hash = (
            "0" * 64 if mutation == "wire_hash" else hashlib.sha256(encoded.encode()).hexdigest()
        )
        try:
            async with factory() as session, session.begin():
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
            if getattr(error.orig, "sqlstate", None) not in {"22023", "23514", "42501"}:
                raise
        else:
            raise AssertionError("Native mixed SQL accepted a malicious completion envelope")
        assert snapshot(admin, fixture) == before


async def check_fixture(
    admin: AdminAPI,
    fixture: NativeCashFixture,
    app_url: URL,
    diagnostic: Diagnostic,
    maintenance: AsyncEngine,
    app_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.domains.sync.offline_auth import OfflineAuthContractError
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(
        app_url.set(username=fixture.node_role, password=fixture.password),
        pool_size=1,
        max_overflow=0,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    operation = fixture.command.confirmation.operation_id
    try:
        diagnostic.phase = "mixed_transaction_modes"
        await transaction_proof(engine, admin, fixture, complete_call=complete)
        diagnostic.phase = "mixed_malicious_envelopes"
        await assert_envelope_denials(factory, admin, fixture)
        diagnostic.phase = "mixed_four_financial_faults"
        await fault_proof(
            factory,
            admin,
            fixture,
            complete_call=complete,
            fault_points=(
                ("batch_movement", "source_id", "AFTER", fixture.command.confirmation.sale_id),
                ("unit_stock_movement_v2", "command_id", "AFTER", operation),
                ("sale_payment", "id", "AFTER", operation),
                ("edge_cash_command", "operation_id", "BEFORE", operation),
            ),
        )
        diagnostic.phase = "mixed_waiting_login_revocation"
        await waiting_proof(factory, maintenance, admin, fixture, complete_call=complete)
        diagnostic.phase = "mixed_late_authority_rollback"
        before = snapshot(admin, fixture)
        try:
            await complete(factory, fixture, fail_call=3)
        except OfflineAuthContractError:
            pass
        else:
            raise AssertionError("Native mixed cash ignored final authorization failure")
        assert snapshot(admin, fixture) == before
        diagnostic.phase = "mixed_concurrent_cash_completion"
        first, second = await asyncio.gather(complete(factory, fixture), complete(factory, fixture))
        assert first == second
        assert_posted(admin, fixture, first)
        before = snapshot(admin, fixture)
        assert await complete(factory, fixture) == first
        assert snapshot(admin, fixture) == before
        diagnostic.phase = "mixed_direct_access_denials"
        await denial_proof(
            engine,
            extra_statements=(
                "SELECT * FROM public.batch",
                "SELECT * FROM public.pos_mixed_checkout",
                "UPDATE public.batch SET qty_remaining=qty_remaining",
                "UPDATE public.register SET id=id",
                "SELECT public.complete_edge_mixed_cash_v1("
                "NULL::uuid,NULL::uuid,1,NULL::uuid,'{}'::jsonb,repeat('a',64))",
            ),
        )
        assert snapshot(admin, fixture) == before
        diagnostic.phase = "historical_app_shift_close"
        await app_close(app_factory, fixture, first)
        diagnostic.phase = "mixed_closed_shift_replay"
        before = snapshot(admin, fixture)
        assert await complete(factory, fixture) == first
        assert snapshot(admin, fixture) == before
    finally:
        await engine.dispose()


async def main(diagnostic: Diagnostic | None = None) -> None:
    require_ci_context()

    from app.core.config import get_settings
    from sqlalchemy.engine import URL
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    diagnostic = diagnostic or Diagnostic()
    settings = get_settings()
    parse_url = cast(Callable[[str], URL], _physical["_shared"]["ci_database_url"])
    app_url, support_url = parse_url(settings.DATABASE_URL_APP), parse_url(
        settings.DATABASE_URL_SUPPORT
    )
    migration_url = parse_url(os.environ["DATABASE_URL_MIGRATION"])
    assert app_url.database is not None
    admin = admin_factory(os.environ["POSTGRES_SERVICE_ID"], app_url.database)
    assert admin.run("SELECT current_database()") == app_url.database
    app, support = create_async_engine(app_url, poolclass=NullPool), create_async_engine(
        support_url, poolclass=NullPool
    )
    maintenance = create_async_engine(
        migration_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"role": "aurum_schema_owner"}},
    )
    app_factory = async_sessionmaker(app, expire_on_commit=False)
    fixtures: list[NativeCashFixture] = []
    cases: tuple[tuple[str, tuple[tuple[SaleFormatV2, int], ...], bool], ...] = (
        ("pack", (("pack", 1),), False),
        ("blister", (("blister", 2),), False),
        ("unit", (("unit", 5),), False),
        ("aggregate_shared_pack", (("pack", 1), ("blister", 1), ("unit", 2)), False),
        ("existing_fragment", (("unit", 2),), True),
    )
    try:
        for name, lines, fragment in cases:
            diagnostic.phase = "mixed_app_draft_" + name
            fixture = await draft_factory(
                support,
                maintenance,
                app_factory,
                lines,
                existing_fragment=fragment,
                kind="mixed_cash",
                configure_sale=partial(configure_legacy, aggregate=name == "aggregate_shared_pack"),
            )
            assert_unposted_draft(
                admin, fixture, expected_legacy_rows=2 if name == "aggregate_shared_pack" else 1
            )
            diagnostic.phase = "mixed_synthetic_activation_" + name
            provision_identity(admin, fixture)
            fixtures.append(fixture)
            await check_fixture(admin, fixture, app_url, diagnostic, maintenance, app_factory)
        diagnostic.phase = "genuine_mixed_history_blocks_downgrade"
        from tests.isolation.test_edge_mixed_cash_execution_migration import (
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
        "Native mixed cash execution checks passed "
        "(5 cases, both stocks, genuine monetary anchors, mixed-formats, existing-fragment, "
        "rollback, replay, gates enabled)."
    )


if __name__ == "__main__":
    diagnostic_state = Diagnostic()
    try:
        asyncio.run(main(diagnostic_state))
    except Exception as error:
        print(
            json.dumps(
                {
                    "native_mixed_execution_proof": "failed",
                    "phase": diagnostic_state.phase,
                    "error": type(error).__name__,
                    "failure_site": failure_site(error),
                    "sqlstate": getattr(error, "sqlstate", None)
                    or getattr(getattr(error, "orig", None), "sqlstate", None),
                    **database_diagnostic(error),
                }
            )
        )
        raise SystemExit(1) from None
