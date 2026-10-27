"""Actual checkout journals under app-role RLS in the disposable CI database."""

from typing import Literal
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import AurumError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.pos.models import Sale
from app.domains.pos.retail_checkout_source_repository import RetailCheckoutSourceRepository
from app.domains.pos.retail_checkout_source_service import RetailCheckoutSourceService
from app.domains.pos.retail_sale_events import RetailCheckoutResult
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.pos import test_mixed_electronic_checkout as mixed_electronic
from tests.domains.pos import test_unit_electronic_checkout as physical_electronic
from tests.domains.pos.test_mixed_cash_checkout import complete as complete_mixed_cash
from tests.domains.pos.test_mixed_electronic_checkout import facts
from tests.domains.pos.test_mixed_physical_checkout_preparation import legacy_with_stock
from tests.domains.pos.test_physical_cash_checkout import complete as complete_physical_cash
from tests.domains.pos.test_physical_cash_refund import close_shift
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture
PathKind = Literal["physical-cash", "mixed-cash", "physical-electronic", "mixed-electronic"]
PATHS: tuple[PathKind, ...] = (
    "physical-cash",
    "mixed-cash",
    "physical-electronic",
    "mixed-electronic",
)


async def completed(session: AsyncSession, path: PathKind) -> RetailCheckoutResult:
    if path == "physical-electronic":
        request, identifiers = await physical_electronic.prepared(session, ("cash", "card", "qr"))
        rows = await physical_electronic.attempts(session, request, identifiers)
        await physical_electronic.confirm(session, rows)
        result = await physical_electronic.complete(session, request)
    elif path == "mixed-electronic":
        request, identifiers = await mixed_electronic.prepared(session, ("cash", "card", "qr"))
        rows = await mixed_electronic.attempts(session, request, identifiers)
        await mixed_electronic.confirm(session, request, rows)
        result = await mixed_electronic.complete(session, request)
    else:
        source = await checkout_source(session)
        if path == "mixed-cash":
            await legacy_with_stock(session, source.sale)
        await put_physical(session, source.sale.id)
        request = await saved_request(session, source.sale)
        result = (
            await complete_mixed_cash(session, request)
            if path == "mixed-cash"
            else await complete_physical_cash(session, request)
        )
    await session.flush()
    await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    assert not session.new and not session.dirty and not session.deleted
    return result


async def read(
    session: AsyncSession, result: RetailCheckoutResult, **changes: object
) -> RetailCheckoutResult:
    arguments = {
        "tenant_id": result.tenant_id,
        "sale_id": result.sale_id,
        "operation_id": result.operation_id,
        "actor_id": result.cashier_id,
        "allowed_branch_ids": {result.branch_id},
        **changes,
    }
    return await RetailCheckoutSourceService(RetailCheckoutSourceRepository(session)).read(
        **arguments
    )


async def protected_facts(session: AsyncSession) -> dict[str, list[object]]:
    before = await facts(session)
    tenant_id = runtime(session).stock.tenant_id
    for table in ("register_receipt_counter", "sync_stream", "sync_outbox"):
        before[table] = list(
            (
                await session.execute(
                    text(
                        f"SELECT to_jsonb(row) FROM {table} row WHERE tenant_id=:tenant "
                        "ORDER BY to_jsonb(row)::text"
                    ),
                    {"tenant": tenant_id},
                )
            ).scalars()
        )
    return before


@pytest.mark.parametrize("path", PATHS)
async def test_each_real_persisted_journal_is_recovered_without_more_financial_or_stock_effects(
    db_session: AsyncSession, path: PathKind
) -> None:
    original = await completed(db_session, path)
    before = await protected_facts(db_session)
    marker = await RetailCheckoutSourceRepository(db_session).read(
        original.tenant_id, original.sale_id, original.operation_id
    )
    assert marker is not None
    selected = marker.mixed if path.startswith("mixed") else marker.physical
    assert selected is not None
    assert selected.payment_journal_version == (3 if path.endswith("electronic") else 2)
    assert await read(db_session, original) == original
    assert await read(db_session, original) == original
    assert await protected_facts(db_session) == before
    assert not db_session.new and not db_session.dirty and not db_session.deleted


@pytest.mark.parametrize("path", PATHS)
async def test_historical_source_survives_catalog_changes_closed_shift_and_readonly_tenant(
    db_session: AsyncSession, path: PathKind
) -> None:
    original = await completed(db_session, path)
    await close_shift(db_session, original.shift_id)
    await db_session.execute(
        text(
            "UPDATE tenant_catalog SET base_price=99.99,brand_name='Changed after checkout' "
            "WHERE tenant_id=:tenant"
        ),
        {"tenant": original.tenant_id},
    )
    await db_session.execute(
        text("UPDATE tenant SET status='readonly' WHERE id=:tenant"),
        {"tenant": original.tenant_id},
    )
    before = await protected_facts(db_session)
    assert await read(db_session, original) == original
    assert await protected_facts(db_session) == before


@pytest.mark.parametrize("scope", ["tenant", "actor", "branch", "operation"])
async def test_current_caller_scope_and_exact_operation_are_required_for_saved_source(
    db_session: AsyncSession, scope: str
) -> None:
    original = await completed(db_session, "mixed-electronic")
    before = await protected_facts(db_session)
    value = UUID("00000000-0000-4000-8000-000000000099")
    changes: dict[str, object] = {
        "tenant": {"tenant_id": value},
        "actor": {"actor_id": value},
        "branch": {"allowed_branch_ids": {value}},
        "operation": {"operation_id": value},
    }[scope]
    with pytest.raises((PermissionDeniedError, NotFoundError)):
        await read(db_session, original, **changes)
    assert await protected_facts(db_session) == before


async def test_journal_rls_hides_other_cashiers_even_when_their_tenant_and_branch_match(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    original = await completed(db_session, "mixed-cash")
    context = runtime(db_session)
    other_actor = await seed_receiving_actor(db_engine, maintenance_engine, context.stock)
    before = await protected_facts(db_session)
    await set_receiving_context(db_session, context.stock, other_actor)
    try:
        marker = await RetailCheckoutSourceRepository(db_session).read(
            original.tenant_id, original.sale_id, original.operation_id
        )
        assert marker is None or (marker.mixed is None and marker.physical is None)
        with pytest.raises((PermissionDeniedError, NotFoundError)):
            await read(db_session, original, actor_id=other_actor)
    finally:
        await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await protected_facts(db_session) == before


async def test_pending_identity_map_change_is_not_refreshed_flushed_or_discarded(
    db_session: AsyncSession,
) -> None:
    original = await completed(db_session, "physical-cash")
    sale = await db_session.get(Sale, original.sale_id)
    assert sale is not None
    sale.currency = "USD"
    pending = set(db_session.dirty)
    with pytest.raises(ConflictError, match="clean session"):
        await read(db_session, original)
    assert set(db_session.dirty) == pending
    assert sale.currency == "USD"


async def test_uncompleted_draft_never_becomes_a_source(db_session: AsyncSession) -> None:
    source = await checkout_source(db_session)
    await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    context = runtime(db_session)
    before = await protected_facts(db_session)
    with pytest.raises(AurumError):
        await RetailCheckoutSourceService(RetailCheckoutSourceRepository(db_session)).read(
            tenant_id=context.stock.tenant_id,
            sale_id=source.sale.id,
            operation_id=request.operation_id,
            actor_id=context.actor_id,
            allowed_branch_ids={context.stock.branch_id},
        )
    assert await protected_facts(db_session) == before
