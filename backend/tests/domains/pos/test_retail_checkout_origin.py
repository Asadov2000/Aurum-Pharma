"""Historical origin reads require existing source authority, never current writer guesses."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.core.errors import AurumError, ConflictError, PermissionDeniedError
from app.domains.pos import retail_checkout_origin_service as origin_service
from app.domains.pos.retail_checkout_origin_repository import (
    RetailCheckoutOriginRepository,
    RetailCheckoutOriginSnapshot,
)
from app.domains.pos.retail_checkout_origin_service import RetailCheckoutOriginService
from app.domains.pos.retail_sale_events import RetailCheckoutResult
from tests.domains.pos.test_retail_checkout_source import OPERATION, Session, result
from tests.domains.pos.test_retail_sale_events import Kind


def origin(kind: Kind = "physical") -> RetailCheckoutOriginSnapshot:
    source = result(kind)
    return RetailCheckoutOriginSnapshot(
        tenant_id=source.tenant_id,
        operation_id=OPERATION,
        sale_id=source.sale_id,
        source_kind=kind,
        branch_id=source.branch_id,
        register_id=source.register_id,
        cashier_id=source.cashier_id,
        origin_node_id=UUID(int=95),
        writer_epoch=7,
        created_at=datetime(2026, 10, 27, 10, tzinfo=UTC),
        created_by=source.cashier_id,
    )


class Repository:
    def __init__(self, value: RetailCheckoutOriginSnapshot | None) -> None:
        self.session = Session()
        self.value = value
        self.calls: list[tuple[UUID, UUID, UUID]] = []

    async def read(
        self, tenant_id: UUID, sale_id: UUID, operation_id: UUID
    ) -> RetailCheckoutOriginSnapshot | None:
        assert self.session.autoflush_suppressed
        self.session.events.append("origin")
        self.calls.append((tenant_id, sale_id, operation_id))
        return self.value


async def read(repository: Repository) -> RetailCheckoutOriginSnapshot | None:
    source = result("physical")
    return await RetailCheckoutOriginService(cast(RetailCheckoutOriginRepository, repository)).read(
        tenant_id=source.tenant_id,
        sale_id=source.sale_id,
        operation_id=OPERATION,
        actor_id=source.cashier_id,
        allowed_branch_ids={source.branch_id},
    )


def install_source(
    monkeypatch: pytest.MonkeyPatch,
    repository: Repository,
    kind: Kind = "physical",
    *,
    denied: bool = False,
    revoked: bool = False,
) -> None:
    async def source(_self: object, **arguments: object) -> RetailCheckoutResult:
        assert repository.session.autoflush_suppressed
        expected = result(kind)
        assert arguments == {
            "tenant_id": expected.tenant_id,
            "sale_id": expected.sale_id,
            "operation_id": OPERATION,
            "actor_id": expected.cashier_id,
            "allowed_branch_ids": {expected.branch_id},
        }
        repository.session.events.append("source")
        if denied:
            raise PermissionDeniedError("Source authority denied")
        return expected

    async def actor(_self: object, tenant_id: UUID, actor_id: UUID, branch_id: UUID) -> None:
        assert repository.session.autoflush_suppressed
        assert (tenant_id, actor_id, branch_id) == (
            result(kind).tenant_id,
            result(kind).cashier_id,
            result(kind).branch_id,
        )
        repository.session.events.append("authority")
        if revoked:
            raise PermissionDeniedError("Authority revoked during read")

    monkeypatch.setattr(origin_service.RetailCheckoutSourceService, "read", source)
    monkeypatch.setattr(origin_service.UnitCartRepository, "assert_actor", actor)


@pytest.mark.parametrize("kind", ["physical", "mixed"])
async def test_captured_historical_writer_is_returned_after_source_and_final_authority(
    monkeypatch: pytest.MonkeyPatch, kind: Kind
) -> None:
    saved = origin(kind)
    repository = Repository(saved)
    install_source(monkeypatch, repository, kind)
    assert await read(repository) == saved
    assert repository.session.events == ["source", "origin", "authority"]
    assert repository.calls == [(saved.tenant_id, saved.sale_id, OPERATION)]
    assert not repository.session.autoflush_suppressed
    with pytest.raises(FrozenInstanceError):
        saved.writer_epoch = 8


async def test_valid_old_checkout_without_captured_origin_stays_explicitly_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Repository(None)
    install_source(monkeypatch, repository)
    assert await read(repository) is None
    assert repository.session.events == ["source", "origin", "authority"]


@pytest.mark.parametrize("stage", ["source", "final-authority"])
async def test_current_access_denial_is_not_bypassed_by_captured_metadata(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    repository = Repository(origin())
    install_source(
        monkeypatch, repository, denied=stage == "source", revoked=stage == "final-authority"
    )
    with pytest.raises(PermissionDeniedError):
        await read(repository)
    assert bool(repository.calls) is (stage != "source")


@pytest.mark.parametrize(
    "field",
    [
        "tenant_id",
        "operation_id",
        "sale_id",
        "branch_id",
        "register_id",
        "cashier_id",
        "created_by",
    ],
)
async def test_captured_identity_is_bound_to_the_validated_saved_source(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    repository = Repository(replace(origin(), **{field: UUID(int=99)}))
    install_source(monkeypatch, repository)
    with pytest.raises(AurumError, match="differs from its source"):
        await read(repository)


@pytest.mark.parametrize("change", ["kind", "zero-epoch", "bool-epoch"])
async def test_captured_kind_and_epoch_must_be_consistent(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    changes = {
        "kind": {"source_kind": "mixed"},
        "zero-epoch": {"writer_epoch": 0},
        "bool-epoch": {"writer_epoch": True},
    }[change]
    repository = Repository(replace(origin(), **changes))
    install_source(monkeypatch, repository)
    with pytest.raises(AurumError):
        await read(repository)


@pytest.mark.parametrize("pending", ["new", "dirty", "deleted"])
async def test_existing_source_clean_session_guard_runs_before_any_origin_select(
    pending: str,
) -> None:
    repository = Repository(origin())
    marker = object()
    collection = cast(set[object], getattr(repository.session, pending))
    collection.add(marker)
    with pytest.raises(ConflictError, match="clean session"):
        await read(repository)
    assert collection == {marker}
    assert repository.calls == []


async def test_origin_selector_does_not_join_or_filter_by_current_writer_state() -> None:
    statements: list[Select[tuple[object, ...]]] = []

    class Empty:
        def mappings(self) -> "Empty":
            return self

        def one_or_none(self) -> None:
            return None

    class SessionOnly:
        async def execute(self, statement: Select[tuple[object, ...]]) -> Empty:
            statements.append(statement)
            return Empty()

    saved = origin()
    repository = RetailCheckoutOriginRepository(cast(AsyncSession, SessionOnly()))
    assert await repository.read(saved.tenant_id, saved.sale_id, saved.operation_id) is None
    assert len(statements) == 1
    statement = statements[0]
    sql = str(statement.compile(dialect=postgresql.dialect())).lower()
    assert "pos_retail_checkout_origin" in sql
    assert "join" not in sql and "sync_stream" not in sql and "sync_node" not in sql
    assert "for update" not in sql and "for share" not in sql
    assert "status" not in sql and "result_payload" not in sql
    assert statement.get_execution_options().get("autoflush") is False
