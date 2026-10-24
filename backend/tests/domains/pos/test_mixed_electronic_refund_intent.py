"""Persisted electronic reviews do not create payment, return, or stock facts."""

from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import NotFoundError, PermissionDeniedError
from app.domains.pos.mixed_electronic_refund_intent_repository import (
    MixedElectronicRefundIntentRepository,
)
from app.domains.pos.mixed_electronic_refund_intent_schemas import (
    SaveMixedElectronicRefundIntentRequest,
)
from app.domains.pos.mixed_electronic_refund_intent_service import (
    MixedElectronicRefundIntentService,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.pos.test_mixed_electronic_refund_preview import (
    financial_facts,
    original,
    preview,
    selection,
)
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def review_facts(session: AsyncSession) -> dict[str, list[object]]:
    result = await financial_facts(session)
    result["audit_log"] = [
        row
        for row in result["audit_log"]
        if not isinstance(row, dict)
        or row.get("table_name") != "pos_mixed_electronic_refund_intent"
    ]
    return result


async def save_review(
    session: AsyncSession, actor: UUID | None = None
) -> SaveMixedElectronicRefundIntentRequest:
    source = await original(session, ("cash", "card", "qr"))
    quote = await preview(session, source, actor=actor)
    request = SaveMixedElectronicRefundIntentRequest.model_validate(
        {
            "operation_id": uuid4(),
            "request": {
                "parent_sale_id": source.sale_id,
                "selection": selection(source),
                "expected_total_amount": quote.total_amount,
                "expected_revision_hash": quote.revision_hash,
            },
        }
    )
    return request


async def test_saved_review_replay_and_read_preserve_time_and_financial_facts(
    db_session: AsyncSession,
) -> None:
    request = await save_review(db_session)
    context = runtime(db_session)
    service = MixedElectronicRefundIntentService(MixedElectronicRefundIntentRepository(db_session))
    before = await review_facts(db_session)
    arguments = {
        "tenant_id": context.stock.tenant_id,
        "actor_id": context.actor_id,
        "allowed_branch_ids": {context.stock.branch_id},
    }
    saved = await service.save(**arguments, request=request)
    assert await service.save(**arguments, request=request) == saved
    assert (
        await service.read(
            **arguments,
            parent_sale_id=request.request.parent_sale_id,
            operation_id=request.operation_id,
        )
        == saved
    )
    assert saved.saved_at.tzinfo is not None
    audit_rows = list(
        (
            await db_session.execute(
                text(
                    "SELECT new_values FROM audit_log "
                    "WHERE table_name='pos_mixed_electronic_refund_intent' AND record_id=:id"
                ),
                {"id": request.operation_id},
            )
        ).scalars()
    )
    assert len(audit_rows) == 1
    assert isinstance(audit_rows[0], dict)
    assert "request_payload" not in audit_rows[0]
    assert "result_payload" not in audit_rows[0]
    assert await review_facts(db_session) == before


@pytest.mark.parametrize("methods", [("card",), ("qr",), ("cash", "card", "qr")])
async def test_refund_actor_can_save_review_of_another_cashiers_sale(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    methods: tuple[str, ...],
) -> None:
    source = await original(db_session, methods)
    context = runtime(db_session)
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    assert actor != source.cashier_id
    await set_receiving_context(db_session, context.stock, actor)
    component_ids = set(
        (
            await db_session.execute(
                text("SELECT id FROM pos_unit_checkout_payment_component WHERE sale_id=:sale"),
                {"sale": source.sale_id},
            )
        ).scalars()
    )
    assert component_ids == {payment.id for payment in source.payments}
    quote = await preview(db_session, source, actor=actor)
    request = SaveMixedElectronicRefundIntentRequest.model_validate(
        {
            "operation_id": uuid4(),
            "request": {
                "parent_sale_id": source.sale_id,
                "selection": selection(source),
                "expected_total_amount": quote.total_amount,
                "expected_revision_hash": quote.revision_hash,
            },
        }
    )
    service = MixedElectronicRefundIntentService(MixedElectronicRefundIntentRepository(db_session))
    before = await review_facts(db_session)
    saved = await service.save(
        tenant_id=context.stock.tenant_id,
        actor_id=actor,
        allowed_branch_ids={context.stock.branch_id},
        request=request,
    )
    assert saved.preview == quote
    assert await review_facts(db_session) == before


@pytest.mark.parametrize("denial", ["no_permission", "foreign_tenant", "foreign_branch", "blocked"])
async def test_mixed_refund_component_raw_select_is_scoped(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    denial: str,
) -> None:
    source = await original(db_session, ("cash", "card", "qr"))
    context = runtime(db_session)
    await db_session.commit()
    stock = context.stock
    if denial == "foreign_branch":
        branch_id = uuid4()
        async with db_engine.begin() as support:
            assert str(await support.scalar(text("SELECT current_database()"))).endswith("_test")
            await support.execute(text("SELECT set_config('app.support_session','true',true)"))
            await support.execute(
                text(
                    "INSERT INTO branch(id,tenant_id,name) "
                    "VALUES (:id,:tenant,'Other test branch')"
                ),
                {"id": branch_id, "tenant": stock.tenant_id},
            )
        stock = replace(stock, branch_id=branch_id)
    actor = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        stock,
        permissions=() if denial == "no_permission" else ("pos.refund",),
    )
    if denial == "blocked":
        async with maintenance_engine.begin() as owner:
            assert str(await owner.scalar(text("SELECT current_database()"))).endswith("_test")
            await owner.execute(
                text("UPDATE app_user SET status='blocked' WHERE id=:id"), {"id": actor}
            )
    await set_receiving_context(db_session, stock, actor)
    if denial == "foreign_tenant":
        await db_session.execute(
            text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(uuid4())}
        )
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_unit_checkout_payment_component WHERE sale_id=:sale"),
            {"sale": source.sale_id},
        )
        == 0
    )


async def test_blocked_actor_cannot_recover_previously_saved_review(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
) -> None:
    request = await save_review(db_session)
    context = runtime(db_session)
    service = MixedElectronicRefundIntentService(MixedElectronicRefundIntentRepository(db_session))
    await service.save(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        request=request,
    )
    before = await review_facts(db_session)
    assert str(await db_session.scalar(text("SELECT current_database()"))).endswith("_test")
    await db_session.commit()
    async with maintenance_engine.begin() as owner:
        await owner.execute(
            text("UPDATE app_user SET status='blocked' WHERE id=:id"), {"id": context.actor_id}
        )
    try:
        await set_receiving_context(db_session, context.stock, context.actor_id)
        with pytest.raises(PermissionDeniedError):
            await service.read(
                tenant_id=context.stock.tenant_id,
                actor_id=context.actor_id,
                allowed_branch_ids={context.stock.branch_id},
                parent_sale_id=request.request.parent_sale_id,
                operation_id=request.operation_id,
            )
        await db_session.rollback()
    finally:
        async with maintenance_engine.begin() as owner:
            await owner.execute(
                text("UPDATE app_user SET status='active' WHERE id=:id"), {"id": context.actor_id}
            )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await review_facts(db_session) == before


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE pos_mixed_electronic_refund_intent SET request_hash=repeat('0',64) WHERE id=:id",
        "DELETE FROM pos_mixed_electronic_refund_intent WHERE id=:id",
    ],
)
async def test_saved_review_rejects_raw_mutation(db_session: AsyncSession, statement: str) -> None:
    request = await save_review(db_session)
    context = runtime(db_session)
    service = MixedElectronicRefundIntentService(MixedElectronicRefundIntentRepository(db_session))
    saved = await service.save(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        request=request,
    )
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(text(statement), {"id": request.operation_id})
    assert (
        await service.read(
            tenant_id=context.stock.tenant_id,
            actor_id=context.actor_id,
            allowed_branch_ids={context.stock.branch_id},
            parent_sale_id=request.request.parent_sale_id,
            operation_id=request.operation_id,
        )
        == saved
    )


async def test_other_refund_actor_cannot_see_saved_review(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    request = await save_review(db_session)
    context = runtime(db_session)
    service = MixedElectronicRefundIntentService(MixedElectronicRefundIntentRepository(db_session))
    await service.save(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        request=request,
    )
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, context.stock, actor)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_mixed_electronic_refund_intent WHERE id=:id"),
            {"id": request.operation_id},
        )
        == 0
    )
    with pytest.raises(NotFoundError):
        await service.read(
            tenant_id=context.stock.tenant_id,
            actor_id=actor,
            allowed_branch_ids={context.stock.branch_id},
            parent_sale_id=request.request.parent_sale_id,
            operation_id=request.operation_id,
        )


@pytest.mark.parametrize(
    "damage",
    [
        "jsonb_set(result_payload,'{branch_id}',to_jsonb(CAST(:wrong AS text)))",
        "jsonb_set(result_payload,'{payments,0,amount}','\"999.00\"')",
    ],
)
async def test_raw_insert_rejects_mismatched_review_snapshot(
    db_session: AsyncSession,
    damage: str,
) -> None:
    request = await save_review(db_session)
    context = runtime(db_session)
    service = MixedElectronicRefundIntentService(MixedElectronicRefundIntentRepository(db_session))
    await service.save(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        request=request,
    )
    columns = (
        "tenant_id,id,parent_sale_id,branch_id,actor_id,request_hash,"
        "request_payload,result_payload,created_by"
    )
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    f"INSERT INTO pos_mixed_electronic_refund_intent ({columns}) "
                    "SELECT tenant_id,:new_id,parent_sale_id,branch_id,actor_id,request_hash,"
                    f"request_payload,{damage},created_by FROM pos_mixed_electronic_refund_intent "
                    "WHERE id=:id"
                ),
                {"new_id": uuid4(), "id": request.operation_id, "wrong": str(uuid4())},
            )
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_mixed_electronic_refund_intent WHERE tenant_id=:tenant"),
            {"tenant": context.stock.tenant_id},
        )
        == 1
    )


async def test_readonly_tenant_can_recover_but_cannot_insert_review(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
) -> None:
    request = await save_review(db_session)
    context = runtime(db_session)
    service = MixedElectronicRefundIntentService(MixedElectronicRefundIntentRepository(db_session))
    saved = await service.save(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        request=request,
    )
    await db_session.commit()
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text("UPDATE tenant SET status='readonly' WHERE id=:id"),
            {"id": context.stock.tenant_id},
        )
    try:
        await set_receiving_context(db_session, context.stock, context.actor_id)
        assert (
            await service.read(
                tenant_id=context.stock.tenant_id,
                actor_id=context.actor_id,
                allowed_branch_ids={context.stock.branch_id},
                parent_sale_id=request.request.parent_sale_id,
                operation_id=request.operation_id,
            )
            == saved
        )
        with pytest.raises(DBAPIError):
            await service.save(
                tenant_id=context.stock.tenant_id,
                actor_id=context.actor_id,
                allowed_branch_ids={context.stock.branch_id},
                request=request.model_copy(update={"operation_id": uuid4()}),
            )
    finally:
        await db_session.rollback()
        # This fresh synthetic tenant stays readonly, as in the other POS fixtures.
