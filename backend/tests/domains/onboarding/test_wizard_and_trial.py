"""onboarding wizard + checklist + trial gate."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.onboarding.repository import OnboardingRepository
from app.domains.onboarding.service import OnboardingService


async def _make_tenant(db_session: AsyncSession):  # type: ignore[no-untyped-def]
    nick = uuid4().hex[:6]
    foundation = FoundationService(FoundationRepository(db_session))
    return await foundation.create_tenant(
        payload={"name": f"T-{nick}", "contact_email": f"t-{nick}@aurum.tj"}
    )


async def test_wizard_state_created_with_tenant(
    db_session: AsyncSession,
) -> None:
    tenant = await _make_tenant(db_session)
    service = OnboardingService(OnboardingRepository(db_session))

    wizard = await service.get_wizard(tenant.id)
    assert wizard.current_step == 1
    assert wizard.is_completed is False
    assert wizard.steps_completed == []

    checklist = await service.get_checklist(tenant.id)
    assert checklist.catalog_items_count == 0
    assert checklist.trial_eligible is False


async def test_submit_step_records_data_and_advances(
    db_session: AsyncSession,
) -> None:
    tenant = await _make_tenant(db_session)
    service = OnboardingService(OnboardingRepository(db_session))

    w = await service.submit_step(tenant_id=tenant.id, step=1, data={"name": "Pharmacy X"})
    assert 1 in w.steps_completed
    assert w.wizard_data["step_1"] == {"name": "Pharmacy X"}
    assert w.current_step == 2  # advanced

    w = await service.submit_step(tenant_id=tenant.id, step=8, data={})
    assert w.is_completed is True
    assert w.completed_at is not None


async def test_step_5_validates_catalog_count(
    db_session: AsyncSession,
) -> None:
    tenant = await _make_tenant(db_session)
    service = OnboardingService(OnboardingRepository(db_session))

    # Not enough catalog items — step 5 must refuse.
    with pytest.raises(BusinessRuleError):
        await service.submit_step(tenant_id=tenant.id, step=5, data={})

    # Update the catalog count and try again.
    await service.update_catalog_count(tenant_id=tenant.id, count=150)
    w = await service.submit_step(tenant_id=tenant.id, step=5, data={})
    assert 5 in w.steps_completed


async def test_track_event_appends_unique(db_session: AsyncSession) -> None:
    tenant = await _make_tenant(db_session)
    service = OnboardingService(OnboardingRepository(db_session))

    await service.track_event(tenant_id=tenant.id, event_name="first_sale")
    await service.track_event(tenant_id=tenant.id, event_name="first_sale")  # dup
    await service.track_event(tenant_id=tenant.id, event_name="shift_opened")

    checklist = await service.get_checklist(tenant.id)
    assert sorted(checklist.completed_tasks) == ["first_sale", "shift_opened"]


async def test_start_trial_blocked_below_100_items(
    db_session: AsyncSession,
) -> None:
    tenant = await _make_tenant(db_session)
    service = OnboardingService(OnboardingRepository(db_session))

    await service.update_catalog_count(tenant_id=tenant.id, count=50)
    with pytest.raises(BusinessRuleError):
        await service.start_trial(tenant_id=tenant.id)


async def test_start_trial_creates_subscription(
    db_session: AsyncSession,
) -> None:
    tenant = await _make_tenant(db_session)
    foundation = FoundationService(FoundationRepository(db_session))
    # Need at least one active branch so amount > 0
    await foundation.create_branch(tenant_id=tenant.id, fields={"name": "B1"})
    service = OnboardingService(OnboardingRepository(db_session))

    await service.update_catalog_count(tenant_id=tenant.id, count=200)
    promoted_tenant, subscription = await service.start_trial(tenant_id=tenant.id)

    assert promoted_tenant.status == "trial"
    assert promoted_tenant.trial_started_at is not None
    assert promoted_tenant.trial_ends_at is not None
    assert subscription.tenant_id == tenant.id
    assert subscription.status == "trial"
    assert subscription.branches_count >= 1

    # Checklist now records the trial moment
    checklist = await service.get_checklist(tenant.id)
    assert checklist.trial_started_at is not None
    assert checklist.trial_eligible is True
