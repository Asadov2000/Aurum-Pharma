"""Routing, authority and non-writing guarantees of the internal checkout source."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.core.errors import AurumError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.pos import retail_checkout_source_service as source_service
from app.domains.pos.mixed_cash_checkout_schemas import MixedCashOperationCompleted
from app.domains.pos.physical_cash_checkout_schemas import PhysicalCashOperationCompleted
from app.domains.pos.retail_checkout_source_repository import (
    RetailCheckoutMarker,
    RetailCheckoutSourceRepository,
    RetailCheckoutSourceSnapshot,
)
from app.domains.pos.retail_checkout_source_service import RetailCheckoutSourceService
from app.domains.pos.retail_sale_events import RetailCheckoutResult
from tests.domains.pos.test_retail_sale_events import Kind
from tests.domains.pos.test_retail_sale_events import result as retail_result

OPERATION = UUID("00000000-0000-4000-8000-000000000008")


class Session:
    def __init__(self) -> None:
        self.new: set[object] = set()
        self.dirty: set[object] = set()
        self.deleted: set[object] = set()
        self.events: list[str] = []
        self.autoflush_suppressed = False

    @property
    @contextmanager
    def no_autoflush(self) -> Iterator[None]:
        self.autoflush_suppressed = True
        try:
            yield
        finally:
            self.autoflush_suppressed = False


class Repository:
    def __init__(self, snapshot: RetailCheckoutSourceSnapshot | None) -> None:
        self.session = Session()
        self.snapshot = snapshot
        self.calls: list[tuple[UUID, UUID, UUID]] = []

    async def read(
        self, tenant_id: UUID, sale_id: UUID, operation_id: UUID
    ) -> RetailCheckoutSourceSnapshot | None:
        assert self.session.autoflush_suppressed
        self.session.events.append("selector")
        self.calls.append((tenant_id, sale_id, operation_id))
        return self.snapshot


def result(kind: Kind) -> RetailCheckoutResult:
    return retail_result(kind).model_copy(
        update={"operation_id": OPERATION, "event_id": OPERATION, "stock_command_id": OPERATION}
    )


def snapshot(kind: Kind = "physical", version: int = 2) -> RetailCheckoutSourceSnapshot:
    value = result(kind)
    marker = RetailCheckoutMarker(
        kind=kind,
        operation_id=OPERATION,
        cash_intent_id=OPERATION if version != 3 else None,
        payment_plan_intent_id=OPERATION if version == 3 else None,
        payment_journal_version=version,
    )
    return RetailCheckoutSourceSnapshot(
        tenant_id=value.tenant_id,
        sale_id=value.sale_id,
        branch_id=value.branch_id,
        cashier_id=value.cashier_id,
        status="completed",
        is_test=False,
        sale_type="sale",
        parent_sale_id=None,
        currency="TJS",
        physical=marker if kind == "physical" else None,
        mixed=marker if kind == "mixed" else None,
    )


async def read(repository: Repository, **changes: object) -> RetailCheckoutResult:
    value = result("physical")
    arguments = {
        "tenant_id": value.tenant_id,
        "sale_id": value.sale_id,
        "operation_id": OPERATION,
        "actor_id": value.cashier_id,
        "allowed_branch_ids": {value.branch_id},
        **changes,
    }
    return await RetailCheckoutSourceService(cast(RetailCheckoutSourceRepository, repository)).read(
        **arguments
    )


def install_readers(
    monkeypatch: pytest.MonkeyPatch,
    repository: Repository,
    value: RetailCheckoutResult,
    *,
    deny_actor: bool = False,
    deny_after_delegate: bool = False,
    completed: bool = True,
) -> list[str]:
    routes: list[str] = []

    async def assert_actor(
        _self: object, tenant_id: UUID, actor_id: UUID, branch_id: UUID | None = None
    ) -> None:
        assert repository.session.autoflush_suppressed
        repository.session.events.append("actor" if branch_id is None else "branch")
        if deny_actor or (deny_after_delegate and routes):
            raise PermissionDeniedError("Synthetic current actor denial")

    monkeypatch.setattr(source_service.UnitCartRepository, "assert_actor", assert_actor)

    def reader(route: str) -> object:
        async def delegated(_self: object, **arguments: object) -> object:
            assert repository.session.autoflush_suppressed
            assert arguments == {
                "tenant_id": value.tenant_id,
                "sale_id": value.sale_id,
                "operation_id": OPERATION,
                "actor_id": value.cashier_id,
                "allowed_branch_ids": {value.branch_id},
            }
            routes.append(route)
            repository.session.events.append("delegate")
            if route.endswith("electronic") or not completed:
                return value
            # Saved-intent validation belongs to real readers exercised in the
            # integration suite. This stand-in exposes their completed wrapper.
            wrapper = (
                PhysicalCashOperationCompleted
                if route == "physical-cash"
                else MixedCashOperationCompleted
            )
            return wrapper.model_construct(
                operation_id=OPERATION, sale_id=value.sale_id, saved_intent=None, result=value
            )

        return delegated

    for service, route in (
        (source_service.PhysicalCashCheckoutStatusService, "physical-cash"),
        (source_service.MixedCashCheckoutStatusService, "mixed-cash"),
        (source_service.UnitElectronicCheckoutService, "physical-electronic"),
        (source_service.MixedElectronicCheckoutService, "mixed-electronic"),
    ):
        monkeypatch.setattr(service, "read", reader(route))
    return routes


@pytest.mark.parametrize("kind", ["physical", "mixed"])
@pytest.mark.parametrize("version", [1, 2, 3])
async def test_all_existing_journal_versions_use_exact_read_only_delegate(
    monkeypatch: pytest.MonkeyPatch, kind: Kind, version: int
) -> None:
    repository = Repository(snapshot(kind, version))
    value = result(kind)
    routes = install_readers(monkeypatch, repository, value)
    assert await read(repository) == value
    assert routes == [f"{kind}-{'electronic' if version == 3 else 'cash'}"]
    assert repository.session.events == ["actor", "selector", "branch", "delegate", "branch"]
    assert repository.calls == [(value.tenant_id, value.sale_id, OPERATION)]
    assert not repository.session.autoflush_suppressed


@pytest.mark.parametrize("pending", ["new", "dirty", "deleted"])
async def test_pending_caller_objects_are_preserved_and_no_reader_or_actor_query_runs(
    monkeypatch: pytest.MonkeyPatch, pending: str
) -> None:
    repository = Repository(snapshot())
    routes = install_readers(monkeypatch, repository, result("physical"))
    marker = object()
    collection = cast(set[object], getattr(repository.session, pending))
    collection.add(marker)
    with pytest.raises(ConflictError, match="clean session"):
        await read(repository)
    assert collection == {marker}
    assert repository.session.events == []
    assert repository.calls == routes == []


async def test_current_authority_is_checked_before_source_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Repository(snapshot())
    routes = install_readers(monkeypatch, repository, result("physical"), deny_actor=True)
    with pytest.raises(PermissionDeniedError):
        await read(repository)
    assert repository.session.events == ["actor"]
    assert repository.calls == routes == []


async def test_current_branch_permission_is_rechecked_after_the_delegate_await(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Repository(snapshot())
    routes = install_readers(monkeypatch, repository, result("physical"), deny_after_delegate=True)
    with pytest.raises(PermissionDeniedError):
        await read(repository)
    assert routes == ["physical-cash"]
    assert repository.session.events == ["actor", "selector", "branch", "delegate", "branch"]


@pytest.mark.parametrize("change", ["missing", "tenant", "sale", "operation"])
async def test_missing_and_cross_context_sources_fail_closed_before_delegate(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    original = snapshot()
    if change == "missing":
        changed = None
    elif change == "operation":
        assert original.physical is not None
        changed = replace(original, physical=replace(original.physical, operation_id=UUID(int=99)))
    else:
        changed = replace(original, **{f"{change}_id": UUID(int=99)})
    repository = Repository(changed)
    routes = install_readers(monkeypatch, repository, result("physical"))
    with pytest.raises(NotFoundError):
        await read(repository)
    assert routes == []


@pytest.mark.parametrize("scope", ["cashier", "branch"])
async def test_caller_scope_never_selects_or_delegates_a_foreign_checkout(
    monkeypatch: pytest.MonkeyPatch, scope: str
) -> None:
    original = snapshot()
    changed = replace(original, cashier_id=UUID(int=99)) if scope == "cashier" else original
    repository = Repository(changed)
    routes = install_readers(monkeypatch, repository, result("physical"))
    with pytest.raises(PermissionDeniedError):
        await read(repository, **({"allowed_branch_ids": set()} if scope == "branch" else {}))
    assert routes == []


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "draft"},
        {"is_test": True},
        {"sale_type": "refund"},
        {"parent_sale_id": UUID(int=99)},
        {"currency": "USD"},
        {"physical": None},
    ],
    ids=["pending", "test-sale", "refund", "parent", "currency", "no-marker"],
)
async def test_only_real_completed_original_sale_metadata_reaches_reader(
    monkeypatch: pytest.MonkeyPatch, changes: dict[str, object]
) -> None:
    repository = Repository(replace(snapshot(), **changes))
    routes = install_readers(monkeypatch, repository, result("physical"))
    with pytest.raises(AurumError):
        await read(repository)
    assert routes == []


@pytest.mark.parametrize(
    "change", ["both", "kind", "version", "bool", "cash-intent", "dual-intent"]
)
async def test_ambiguous_or_incompatible_marker_is_rejected_before_dispatch(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    original = snapshot()
    assert original.physical is not None
    marker = original.physical
    if change == "both":
        changed = replace(original, mixed=replace(marker, kind="mixed"))
    else:
        values: dict[str, object] = {
            "kind": {"kind": "mixed"},
            "version": {"payment_journal_version": 4},
            "bool": {"payment_journal_version": True},
            "cash-intent": {"cash_intent_id": None},
            "dual-intent": {"payment_plan_intent_id": OPERATION},
        }[change]
        changed = replace(original, physical=replace(marker, **values))
    repository = Repository(changed)
    routes = install_readers(monkeypatch, repository, result("physical"))
    with pytest.raises(AurumError):
        await read(repository)
    assert routes == []


@pytest.mark.parametrize("kind", ["physical", "mixed"])
async def test_cash_path_requires_the_completed_wrapper_from_existing_validator(
    monkeypatch: pytest.MonkeyPatch, kind: Kind
) -> None:
    repository = Repository(snapshot(kind))
    install_readers(monkeypatch, repository, result(kind), completed=False)
    with pytest.raises(AurumError, match="not completed"):
        await read(repository)


@pytest.mark.parametrize(
    "field",
    [
        "branch_id",
        "tenant_id",
        "sale_id",
        "cashier_id",
        "operation_id",
        "event_id",
        "stock_command_id",
        "is_test",
    ],
)
async def test_result_returned_by_delegate_is_bound_again_to_the_scalar_source(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    repository = Repository(snapshot(version=3))
    value = result("physical").model_copy(
        update={field: True if field == "is_test" else UUID(int=99)}
    )
    # Keep delegate arguments bound to the source, independently from its result.
    install_readers(monkeypatch, repository, result("physical"))

    async def forged(_self: object, **_arguments: object) -> RetailCheckoutResult:
        return value

    monkeypatch.setattr(source_service.UnitElectronicCheckoutService, "read", forged)
    with pytest.raises(AurumError):
        await read(repository)


async def test_delegate_cannot_return_an_unvalidated_constructed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Repository(snapshot(version=3))
    value = result("physical")
    value = value.model_copy(update={"total_amount": "not-money"})
    install_readers(monkeypatch, repository, result("physical"))

    async def forged(_self: object, **_arguments: object) -> RetailCheckoutResult:
        return value

    monkeypatch.setattr(source_service.UnitElectronicCheckoutService, "read", forged)
    with pytest.raises(AurumError, match="invalid checkout result"):
        await read(repository)


async def test_scalar_selector_has_exact_scope_without_blob_loads_locks_or_orm_refresh() -> None:
    statements: list[Select[tuple[object, ...]]] = []

    class EmptyResult:
        def mappings(self) -> "EmptyResult":
            return self

        def one_or_none(self) -> None:
            return None

    class SelectOnlySession:
        async def execute(self, statement: Select[tuple[object, ...]]) -> EmptyResult:
            statements.append(statement)
            return EmptyResult()

    repository = RetailCheckoutSourceRepository(cast(AsyncSession, SelectOnlySession()))
    value = result("physical")
    assert await repository.read(value.tenant_id, value.sale_id, OPERATION) is None
    assert len(statements) == 1
    statement = statements[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert set(compiled.params.values()) == {value.tenant_id, value.sale_id, OPERATION}
    sql = str(compiled).lower()
    assert "sale.tenant_id =" in sql and "sale.id =" in sql
    assert "pos_physical_checkout.id =" in sql and "pos_mixed_checkout.id =" in sql
    assert "result_payload" not in sql and "receipt_payload" not in sql
    assert "for update" not in sql and "for share" not in sql
    assert not statement.get_execution_options().get("populate_existing", False)
    assert statement.get_execution_options().get("autoflush") is False
