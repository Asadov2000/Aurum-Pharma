"""Canonical onboarding readiness and owner launch without subscriptions."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi import Request
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.deps import _seed_request_db_context, get_db
from app.core.errors import (
    BusinessRuleError,
    ConflictError,
    PermissionDeniedError,
    SubscriptionsDisabledError,
)
from app.domains.billing.models import TenantSubscription
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.onboarding.models import TenantLaunchActivation
from app.domains.onboarding.repository import OnboardingRepository
from app.domains.onboarding.service import OnboardingService
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from tests.auth_helpers import create_session_access_token


async def _make_tenant(db_session: AsyncSession):  # type: ignore[no-untyped-def]
    nick = uuid4().hex[:8]
    foundation = FoundationService(FoundationRepository(db_session))
    return await foundation.create_tenant(
        payload={"name": f"T-{nick}", "contact_email": f"t-{nick}@aurum.tj"}
    )


async def _add_owner(db_session: AsyncSession, tenant_id: UUID) -> UUID:
    nick = uuid4().hex[:8]
    user_id = (
        await db_session.execute(
            text("""
                INSERT INTO public.app_user (
                  email, full_name, status, home_tenant_id, activated_at
                ) VALUES (
                  :email, 'Test Owner', 'active', :tenant_id, statement_timestamp()
                )
                RETURNING id
                """),
            {"email": f"owner-{nick}@aurum.tj", "tenant_id": tenant_id},
        )
    ).scalar_one()
    membership_id = (
        await db_session.execute(
            text("""
                INSERT INTO public.tenant_membership (
                  tenant_id, user_id, full_name, status, activated_at
                ) VALUES (
                  :tenant_id, :user_id, 'Test Owner', 'active', statement_timestamp()
                )
                RETURNING id
                """),
            {"tenant_id": tenant_id, "user_id": user_id},
        )
    ).scalar_one()
    await db_session.execute(
        text("""
            INSERT INTO public.tenant_ownership (tenant_id, membership_id, is_active)
            VALUES (:tenant_id, :membership_id, true)
            """),
        {"tenant_id": tenant_id, "membership_id": membership_id},
    )
    return user_id


async def _add_catalog_items(
    db_session: AsyncSession,
    tenant_id: UUID,
    *,
    count: int = 100,
) -> None:
    await db_session.execute(
        text("""
            INSERT INTO public.tenant_catalog (
              tenant_id, brand_name, dispensing_type, storage_type, is_active
            )
            SELECT
              :tenant_id,
              'Ready item ' || series.item,
              'otc',
              'normal',
              true
            FROM pg_catalog.generate_series(1, :count) AS series(item)
            """),
        {"tenant_id": tenant_id, "count": count},
    )


async def _prepare_ready_tenant(db_session: AsyncSession, tenant_id: UUID) -> UUID:
    owner_user_id = await _add_owner(db_session, tenant_id)
    foundation = FoundationService(FoundationRepository(db_session))
    branch = await foundation.create_branch(
        tenant_id=tenant_id,
        fields={
            "name": "Main",
            "address": "Dushanbe, Rudaki 1",
            "license_number": f"LIC-{uuid4().hex[:8]}",
            "license_expires_at": date.today() + timedelta(days=365),
            "receipt_header": {"line1": "Aurum Test Pharmacy"},
        },
    )
    await foundation.create_register(
        tenant_id=tenant_id,
        fields={"branch_id": branch.id, "name": "Register 1"},
    )
    await _add_catalog_items(db_session, tenant_id)
    return owner_user_id


async def _set_actor_context(
    db_session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    session_id: UUID,
) -> None:
    await db_session.execute(
        text("""
            INSERT INTO public.session (
              id, user_id, refresh_token_hash, expires_at
            ) VALUES (
              :session_id, :user_id, :token_hash,
              statement_timestamp() + interval '1 day'
            )
            """),
        {
            "session_id": session_id,
            "user_id": user_id,
            "token_hash": uuid4().hex + uuid4().hex,
        },
    )
    for key, value in (
        ("app.tenant_id", tenant_id),
        ("app.user_id", user_id),
        ("app.auth_session_id", session_id),
    ):
        await db_session.execute(
            text("SELECT pg_catalog.set_config(:key, :value, true)"),
            {"key": key, "value": str(value)},
        )


async def test_wizard_state_created_with_tenant(db_session: AsyncSession) -> None:
    tenant = await _make_tenant(db_session)
    service = OnboardingService(OnboardingRepository(db_session))

    wizard = await service.get_wizard(tenant.id)
    assert wizard.current_step == 1
    assert wizard.is_completed is False
    assert wizard.steps_completed == []

    checklist = await service.get_checklist(tenant.id)
    assert checklist.catalog_items_count == 0
    assert checklist.trial_eligible is False


async def test_submit_step_records_data_and_rejects_skips(
    db_session: AsyncSession,
) -> None:
    tenant = await _make_tenant(db_session)
    service = OnboardingService(OnboardingRepository(db_session))

    wizard = await service.submit_step(
        tenant_id=tenant.id,
        step=1,
        data={"name": "Pharmacy X"},
    )
    assert wizard.steps_completed == [1]
    assert wizard.wizard_data["step_1"] == {"name": "Pharmacy X"}
    assert wizard.current_step == 2

    with pytest.raises(BusinessRuleError):
        await service.submit_step(tenant_id=tenant.id, step=3, data={})


async def test_step_5_validates_catalog_count(db_session: AsyncSession) -> None:
    tenant = await _make_tenant(db_session)
    service = OnboardingService(OnboardingRepository(db_session))
    for step in range(1, 5):
        await service.submit_step(tenant_id=tenant.id, step=step, data={})

    with pytest.raises(BusinessRuleError):
        await service.submit_step(tenant_id=tenant.id, step=5, data={})

    await _add_catalog_items(db_session, tenant.id)
    wizard = await service.submit_step(tenant_id=tenant.id, step=5, data={})
    assert 5 in wizard.steps_completed


async def test_track_event_appends_unique(db_session: AsyncSession) -> None:
    tenant = await _make_tenant(db_session)
    service = OnboardingService(OnboardingRepository(db_session))

    await service.track_event(tenant_id=tenant.id, event_name="first_sale")
    await service.track_event(tenant_id=tenant.id, event_name="first_sale")
    await service.track_event(tenant_id=tenant.id, event_name="shift_opened")

    checklist = await service.get_checklist(tenant.id)
    assert sorted(checklist.completed_tasks) == ["first_sale", "shift_opened"]


async def test_overview_uses_canonical_domain_data(db_session: AsyncSession) -> None:
    tenant = await _make_tenant(db_session)
    service = OnboardingService(OnboardingRepository(db_session))
    await service.update_catalog_count(tenant_id=tenant.id, count=500)

    incomplete = await service.get_overview(tenant.id)
    catalog = next(step for step in incomplete.steps if step.code == "catalog")
    assert catalog.current == 0
    assert catalog.is_complete is False

    await _prepare_ready_tenant(db_session, tenant.id)
    ready = await service.get_overview(tenant.id)
    assert ready.is_ready is True
    assert ready.can_start_trial is False
    assert ready.can_launch is True
    assert ready.launched_at is None
    assert ready.required_completed == ready.required_total
    assert ready.recommended_total == 5
    assert all(task.code != "catalog_loaded" for task in ready.recommended_tasks)


async def test_recommended_first_sale_accepts_a_live_sale(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    scaffold = await pos_scaffold(tenant_status="active", sale_price=10)
    pos = POSService(POSRepository(db_session))
    await pos.open_shift(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        opened_by_user_id=scaffold["cashier"].id,
        opening_cash=Decimal("0"),
    )
    sale = await pos.create_sale(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
    )
    await pos.add_item(sale_id=sale.id, catalog_id=scaffold["item"].id, qty=Decimal("1"))
    await pos.add_payment(sale_id=sale.id, payment_method="cash", amount=Decimal("10"))
    completed = await pos.complete(sale_id=sale.id)
    assert completed.is_test is False

    overview = await OnboardingService(OnboardingRepository(db_session)).get_overview(
        scaffold["tenant"].id
    )
    first_sale = next(task for task in overview.recommended_tasks if task.code == "first_sale")
    assert first_sale.is_complete is True


async def test_overview_requires_one_fully_operational_branch(
    db_session: AsyncSession,
) -> None:
    tenant = await _make_tenant(db_session)
    await _add_owner(db_session, tenant.id)
    await _add_catalog_items(db_session, tenant.id)
    foundation = FoundationService(FoundationRepository(db_session))
    await foundation.create_branch(
        tenant_id=tenant.id,
        fields={
            "name": "Licensed only",
            "address": "Dushanbe, Rudaki 2",
            "license_number": f"LIC-{uuid4().hex[:8]}",
            "license_expires_at": date.today() + timedelta(days=365),
        },
    )
    receipt_only = await foundation.create_branch(
        tenant_id=tenant.id,
        fields={
            "name": "Receipt only",
            "address": "Dushanbe, Rudaki 3",
            "receipt_header": {"line1": "Aurum Test Pharmacy"},
        },
    )
    await foundation.create_register(
        tenant_id=tenant.id,
        fields={"branch_id": receipt_only.id, "name": "Register 2"},
    )

    overview = await OnboardingService(OnboardingRepository(db_session)).get_overview(tenant.id)
    by_code = {step.code: step for step in overview.steps}
    assert by_code["licensed_branch"].is_complete is True
    assert by_code["receipt_details"].is_complete is False
    assert by_code["pos_settings"].is_complete is False
    assert by_code["pos_settings"].current == 0
    assert overview.is_ready is False


async def test_overview_rejects_inactive_owner_account(db_session: AsyncSession) -> None:
    tenant = await _make_tenant(db_session)
    owner_user_id = await _prepare_ready_tenant(db_session, tenant.id)
    await db_session.execute(
        text("UPDATE public.app_user SET status = 'blocked' WHERE id = :user_id"),
        {"user_id": owner_user_id},
    )

    overview = await OnboardingService(OnboardingRepository(db_session)).get_overview(tenant.id)
    owner_step = next(step for step in overview.steps if step.code == "tenant_owner")
    assert owner_step.is_complete is False
    assert overview.can_start_trial is False


async def test_retired_trial_endpoint_service_is_explicitly_disabled(
    db_session: AsyncSession,
) -> None:
    tenant = await _make_tenant(db_session)
    service = OnboardingService(OnboardingRepository(db_session))
    with pytest.raises(SubscriptionsDisabledError) as error:
        await service.start_trial(tenant_id=tenant.id, source="automatic", operation_id=uuid4())
    assert error.value.code == "subscriptions_disabled"
    assert (
        await db_session.scalar(
            select(func.count(TenantSubscription.id)).where(
                TenantSubscription.tenant_id == tenant.id
            )
        )
        == 0
    )


async def test_launch_http_contract_and_retired_trial_error(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    from app.main import app

    tenant = await _make_tenant(db_session)
    owner_id = await _prepare_ready_tenant(db_session, tenant.id)
    token = await create_session_access_token(db_session, user_id=owner_id, tenant_id=tenant.id)

    async def owner_db(request: Request) -> AsyncIterator[AsyncSession]:
        await _seed_request_db_context(request, db_session)
        yield db_session

    app.dependency_overrides[get_db] = owner_db
    try:
        headers = {"Authorization": f"Bearer {token}"}
        operation_id = str(uuid4())
        retired = await client.post(
            "/api/v1/onboarding/start-trial", headers=headers, json={"operation_id": operation_id}
        )
        assert retired.status_code == 422
        assert retired.json()["error"]["code"] == "subscriptions_disabled"
        response = await client.post(
            "/api/v1/onboarding/launch", headers=headers, json={"operation_id": operation_id}
        )
        assert response.status_code == 201
        result = response.json()
        assert set(result) == {"tenant_id", "status", "launched_at", "operation_id"}
        assert result["tenant_id"] == str(tenant.id)
        assert result["status"] == "active"
        assert result["operation_id"] == operation_id
        assert result["launched_at"]
        replay = await client.post(
            "/api/v1/onboarding/launch", headers=headers, json={"operation_id": operation_id}
        )
        assert replay.status_code == 201
        assert replay.json() == result
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_owner_launch_is_single_use_idempotent_and_creates_no_subscription(
    db_session: AsyncSession,
) -> None:
    tenant = await _make_tenant(db_session)
    owner_id = await _prepare_ready_tenant(db_session, tenant.id)
    session_id = uuid4()
    await _set_actor_context(
        db_session, tenant_id=tenant.id, user_id=owner_id, session_id=session_id
    )
    service = OnboardingService(OnboardingRepository(db_session))
    operation_id = uuid4()
    arguments = {
        "tenant_id": tenant.id,
        "operation_id": operation_id,
        "actor_user_id": owner_id,
        "actor_session_id": session_id,
    }
    first = await service.launch(**arguments)
    replay = await service.launch(**arguments)
    assert replay == first
    assert first.status == "active"
    assert first.operation_id == operation_id
    await db_session.refresh(tenant)
    assert tenant.status == "active"
    assert tenant.trial_started_at is None
    assert tenant.trial_ends_at is None
    assert (
        await db_session.scalar(
            select(func.count(TenantSubscription.id)).where(
                TenantSubscription.tenant_id == tenant.id
            )
        )
        == 0
    )
    activation = await db_session.get(TenantLaunchActivation, tenant.id)
    assert activation is not None
    assert activation.actor_user_id == owner_id
    assert activation.actor_session_id == session_id
    assert activation.launched_at == first.launched_at
    overview = await service.get_overview(tenant.id)
    assert overview.launched_at == first.launched_at
    assert not overview.can_launch
    assert not overview.can_start_trial
    with pytest.raises(ConflictError):
        async with db_session.begin_nested():
            await service.launch(**{**arguments, "operation_id": uuid4()})
    for mutation in (
        "UPDATE public.tenant_launch_activation SET launched_at = now() WHERE tenant_id = :id",
        "DELETE FROM public.tenant_launch_activation WHERE tenant_id = :id",
    ):
        with pytest.raises(DBAPIError):
            async with db_session.begin_nested():
                await db_session.execute(text(mutation), {"id": tenant.id})


async def test_launch_requires_canonical_readiness_not_checklist_counters(
    db_session: AsyncSession,
) -> None:
    tenant = await _make_tenant(db_session)
    owner_id = await _add_owner(db_session, tenant.id)
    session_id = uuid4()
    await _set_actor_context(
        db_session, tenant_id=tenant.id, user_id=owner_id, session_id=session_id
    )
    service = OnboardingService(OnboardingRepository(db_session))
    await service.update_catalog_count(tenant_id=tenant.id, count=500)
    with pytest.raises(BusinessRuleError):
        async with db_session.begin_nested():
            await service.launch(
                tenant_id=tenant.id,
                operation_id=uuid4(),
                actor_user_id=owner_id,
                actor_session_id=session_id,
            )
    await db_session.refresh(tenant)
    assert tenant.status == "setup"
    assert await db_session.get(TenantLaunchActivation, tenant.id) is None


@pytest.mark.parametrize(
    "invalid", ["not-owner", "blocked-owner", "revoked-session", "wrong-session"]
)
async def test_launch_requires_real_active_owner_session(
    db_session: AsyncSession, invalid: str
) -> None:
    tenant = await _make_tenant(db_session)
    owner_id = await _prepare_ready_tenant(db_session, tenant.id)
    session_id = uuid4()
    if invalid == "not-owner":
        other_tenant = await _make_tenant(db_session)
        owner_id = await _add_owner(db_session, other_tenant.id)
    await _set_actor_context(
        db_session, tenant_id=tenant.id, user_id=owner_id, session_id=session_id
    )
    if invalid == "blocked-owner":
        await db_session.execute(
            text("UPDATE app_user SET status = 'blocked' WHERE id = :id"), {"id": owner_id}
        )
    elif invalid == "revoked-session":
        await db_session.execute(
            text("UPDATE session SET revoked_at = now() WHERE id = :id"), {"id": session_id}
        )
    elif invalid == "wrong-session":
        session_id = uuid4()
    with pytest.raises(PermissionDeniedError):
        async with db_session.begin_nested():
            await OnboardingService(OnboardingRepository(db_session)).launch(
                tenant_id=tenant.id,
                operation_id=uuid4(),
                actor_user_id=owner_id,
                actor_session_id=session_id,
            )
    assert await db_session.get(TenantLaunchActivation, tenant.id) is None


async def test_database_prevents_setup_active_without_launch_proof(
    db_session: AsyncSession,
) -> None:
    tenant = await _make_tenant(db_session)
    with pytest.raises(DBAPIError, match="Checked owner launch is required"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE tenant SET status = 'active' WHERE id = :id"), {"id": tenant.id}
            )
    await db_session.refresh(tenant)
    assert tenant.status == "setup"


async def test_database_prevents_launch_bypass_through_readonly(db_session: AsyncSession) -> None:
    tenant = await _make_tenant(db_session)
    with pytest.raises(DBAPIError, match="Checked owner launch is required"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE tenant SET status = 'readonly' WHERE id = :id"), {"id": tenant.id}
            )
            await db_session.execute(
                text("UPDATE tenant SET status = 'active' WHERE id = :id"), {"id": tenant.id}
            )


@pytest.mark.parametrize("status", ["trial", "grace_period"])
async def test_database_rejects_new_legacy_subscription_lifecycle(
    db_session: AsyncSession, status: str
) -> None:
    tenant = await _make_tenant(db_session)
    with pytest.raises(DBAPIError, match="Subscriptions are disabled"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE tenant SET status = :status WHERE id = :id"),
                {"id": tenant.id, "status": status},
            )


async def test_launch_proof_is_tenant_isolated_and_not_directly_writable(
    db_engine: AsyncEngine,
) -> None:
    app_engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    tenant_ids: list[UUID] = []
    try:
        async with db_engine.begin() as connection:
            async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                first, second = await _make_tenant(session), await _make_tenant(session)
                tenant_ids = [first.id, second.id]
                owner_id = await _prepare_ready_tenant(session, first.id)
                session_id = uuid4()
                await _set_actor_context(
                    session, tenant_id=first.id, user_id=owner_id, session_id=session_id
                )
                await OnboardingService(OnboardingRepository(session)).launch(
                    tenant_id=first.id,
                    operation_id=uuid4(),
                    actor_user_id=owner_id,
                    actor_session_id=session_id,
                )
        async with app_engine.begin() as connection:
            for key, value in (
                ("app.tenant_id", tenant_ids[0]),
                ("app.user_id", owner_id),
                ("app.auth_session_id", session_id),
            ):
                await connection.execute(
                    text("SELECT set_config(:key, :value, true)"), {"key": key, "value": str(value)}
                )
            assert list(
                await connection.scalars(text("SELECT tenant_id FROM tenant_launch_activation"))
            ) == [tenant_ids[0]]
            await connection.execute(
                text("SELECT set_config('app.tenant_id', :id, true)"), {"id": str(tenant_ids[1])}
            )
            assert not list(
                await connection.scalars(text("SELECT tenant_id FROM tenant_launch_activation"))
            )
            with pytest.raises(DBAPIError):
                async with connection.begin_nested():
                    await connection.execute(
                        text(
                            "INSERT INTO tenant_launch_activation(tenant_id,operation_id,"
                            "actor_user_id,actor_session_id,launched_at) "
                            "VALUES (:id,:op,:owner,:session,now())"
                        ),
                        {
                            "id": tenant_ids[1],
                            "op": uuid4(),
                            "owner": owner_id,
                            "session": session_id,
                        },
                    )
            with pytest.raises(DBAPIError, match="Active owner session is required"):
                async with connection.begin_nested():
                    await connection.execute(
                        text("SELECT public.launch_tenant(:id,:op,:owner,:session)"),
                        {
                            "id": tenant_ids[0],
                            "op": uuid4(),
                            "owner": owner_id,
                            "session": session_id,
                        },
                    )
    finally:
        await app_engine.dispose()
        # This suite runs only in a disposable test database. Keep the unique
        # launch/audit/writer history intact; the runner retires the database.
        # Never disable immutable-history guards merely to clean up fixtures.
