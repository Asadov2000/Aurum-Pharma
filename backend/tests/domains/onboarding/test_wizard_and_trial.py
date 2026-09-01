"""Canonical onboarding readiness and single-use trial activation."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, ConflictError, PermissionDeniedError
from app.domains.billing.models import TenantSubscription
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.onboarding.models import TrialActivation
from app.domains.onboarding.repository import OnboardingRepository
from app.domains.onboarding.service import OnboardingService
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService


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
    assert ready.can_start_trial is True
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


async def test_start_trial_requires_canonical_readiness(db_session: AsyncSession) -> None:
    tenant = await _make_tenant(db_session)
    service = OnboardingService(OnboardingRepository(db_session))
    await service.update_catalog_count(tenant_id=tenant.id, count=500)

    with pytest.raises(BusinessRuleError):
        await service.start_trial(
            tenant_id=tenant.id,
            source="automatic",
            operation_id=uuid4(),
        )


async def test_start_trial_is_single_use_and_idempotent(db_session: AsyncSession) -> None:
    tenant = await _make_tenant(db_session)
    owner_user_id = await _prepare_ready_tenant(db_session, tenant.id)
    owner_session_id = uuid4()
    await _set_actor_context(
        db_session,
        tenant_id=tenant.id,
        user_id=owner_user_id,
        session_id=owner_session_id,
    )
    service = OnboardingService(OnboardingRepository(db_session))
    operation_id = uuid4()

    first = await service.start_trial(
        tenant_id=tenant.id,
        source="manual",
        operation_id=operation_id,
        actor_user_id=owner_user_id,
        actor_session_id=owner_session_id,
    )
    replay = await service.start_trial(
        tenant_id=tenant.id,
        source="manual",
        operation_id=operation_id,
        actor_user_id=owner_user_id,
        actor_session_id=owner_session_id,
    )

    assert first.status == "trial"
    assert replay.subscription_id == first.subscription_id
    assert replay.trial_started_at == first.trial_started_at
    assert (
        await db_session.scalar(
            select(func.count(TenantSubscription.id)).where(
                TenantSubscription.tenant_id == tenant.id
            )
        )
        == 1
    )
    activation = await db_session.get(TrialActivation, tenant.id)
    assert activation is not None
    assert activation.operation_id == operation_id
    assert activation.actor_user_id == owner_user_id

    immutable_probe = await db_session.begin_nested()
    with pytest.raises(DBAPIError):
        await db_session.execute(
            text(
                "UPDATE public.trial_activation SET source = 'automatic' "
                "WHERE tenant_id = :tenant_id"
            ),
            {"tenant_id": tenant.id},
        )
    await immutable_probe.rollback()

    await db_session.execute(
        text(
            "UPDATE public.tenant_subscription SET status = 'archived' "
            "WHERE tenant_id = :tenant_id"
        ),
        {"tenant_id": tenant.id},
    )
    await db_session.execute(
        text(
            "UPDATE public.tenant SET status = 'setup', trial_started_at = NULL, "
            "trial_ends_at = NULL WHERE id = :tenant_id"
        ),
        {"tenant_id": tenant.id},
    )
    with pytest.raises(ConflictError):
        await service.start_trial(
            tenant_id=tenant.id,
            source="manual",
            operation_id=uuid4(),
            actor_user_id=owner_user_id,
            actor_session_id=owner_session_id,
        )


async def test_start_trial_rechecks_active_owner(db_session: AsyncSession) -> None:
    tenant = await _make_tenant(db_session)
    await _prepare_ready_tenant(db_session, tenant.id)
    service = OnboardingService(OnboardingRepository(db_session))

    with pytest.raises(PermissionDeniedError):
        await service.start_trial(
            tenant_id=tenant.id,
            source="manual",
            operation_id=uuid4(),
            actor_user_id=uuid4(),
            actor_session_id=uuid4(),
        )
