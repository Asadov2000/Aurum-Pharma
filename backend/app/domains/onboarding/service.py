"""Business logic for the onboarding domain.

The wizard takes 8 steps; each one writes its raw payload under
`wizard_state.wizard_data["step_N"]` and adds N to
`steps_completed`. Step 5 enforces `catalog_items_count >= 100`
(the per-spec gate for going live), and step 8 flips
`is_completed=true`.

`start_trial` is the explicit, user-driven trial promotion: it
demands `catalog_items_count >= 100`, moves the tenant to `trial`
with a 14-day window, and writes the matching `tenant_subscription`
row. The Celery `auto_start_trials` task (in foundation) is the
implicit, time-based counterpart for setup-tenants that have grown
stale — it consults this same checklist.

`track_event` is the canonical write-side for the post-wizard
checklist (first_sale, second_user, shift_opened, etc.).
Cross-domain callers — catalog, pos, incoming, roles — call this
function via a lazy import to avoid a circular dependency at module
load.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog

from app.core.errors import BusinessRuleError, NotFoundError
from app.core.time import utc_now
from app.domains.onboarding.models import OnboardingChecklist, WizardState
from app.domains.onboarding.repository import OnboardingRepository

logger = structlog.get_logger("onboarding.service")

SETUP_PHASE = timedelta(days=60)
TRIAL_DURATION = timedelta(days=14)
TRIAL_MIN_CATALOG_ITEMS = 100
ALL_STEPS = list(range(1, 9))


class OnboardingService:
    def __init__(self, repo: OnboardingRepository) -> None:
        self.repo = repo

    # =========================================================================
    # Bootstrap (called from FoundationService.create_tenant)
    # =========================================================================

    async def on_tenant_created(self, tenant_id: UUID) -> None:
        """Idempotent: creating a wizard/checklist for the same tenant twice
        is a no-op."""
        if (await self.repo.get_wizard(tenant_id)) is None:
            await self.repo.insert_wizard(tenant_id=tenant_id)
        if (await self.repo.get_checklist(tenant_id)) is None:
            await self.repo.insert_checklist(
                tenant_id=tenant_id, setup_ends_at=utc_now() + SETUP_PHASE
            )

    # =========================================================================
    # Wizard
    # =========================================================================

    async def get_wizard(self, tenant_id: UUID) -> WizardState:
        wizard = await self.repo.get_wizard(tenant_id)
        if wizard is None:
            raise NotFoundError("Wizard not initialised for this tenant")
        return wizard

    async def submit_step(self, *, tenant_id: UUID, step: int, data: dict[str, Any]) -> WizardState:
        if step not in ALL_STEPS:
            raise BusinessRuleError("Wizard step must be 1..8", details={"step": step})
        wizard = await self.get_wizard(tenant_id)
        if wizard.is_completed:
            raise BusinessRuleError("Wizard is already completed")

        # Step 5 gate: catalogue must have ≥100 items.
        if step == 5:
            checklist = await self.repo.get_checklist(tenant_id)
            count = checklist.catalog_items_count if checklist else 0
            if count < TRIAL_MIN_CATALOG_ITEMS:
                raise BusinessRuleError(
                    "Catalog must have at least 100 items before completing step 5",
                    details={"have": count, "need": TRIAL_MIN_CATALOG_ITEMS},
                )

        new_wizard_data = dict(wizard.wizard_data)
        new_wizard_data[f"step_{step}"] = data
        steps = list(wizard.steps_completed)
        if step not in steps:
            steps.append(step)

        fields: dict[str, Any] = {
            "wizard_data": new_wizard_data,
            "steps_completed": steps,
            "current_step": max(wizard.current_step, min(step + 1, 8)),
        }
        if step == 8:
            fields["is_completed"] = True
            fields["completed_at"] = utc_now()
        return await self.repo.update_wizard(wizard, **fields)

    # =========================================================================
    # Checklist + events
    # =========================================================================

    async def get_checklist(self, tenant_id: UUID) -> OnboardingChecklist:
        checklist = await self.repo.get_checklist(tenant_id)
        if checklist is None:
            raise NotFoundError("Checklist not initialised for this tenant")
        return checklist

    async def track_event(self, *, tenant_id: UUID, event_name: str) -> OnboardingChecklist | None:
        """Add `event_name` to completed_tasks if not already there.
        Returns the updated checklist, or None if no checklist exists for
        this tenant (e.g. it was deleted)."""
        checklist = await self.repo.get_checklist(tenant_id)
        if checklist is None:
            return None
        tasks = list(checklist.completed_tasks)
        if event_name in tasks:
            return checklist
        tasks.append(event_name)
        updated = await self.repo.update_checklist(checklist, completed_tasks=tasks)
        logger.info(
            "onboarding_event_tracked",
            tenant_id=str(tenant_id),
            event_name=event_name,
        )
        return updated

    async def update_catalog_count(
        self, *, tenant_id: UUID, count: int
    ) -> OnboardingChecklist | None:
        checklist = await self.repo.get_checklist(tenant_id)
        if checklist is None:
            return None
        eligible = count >= TRIAL_MIN_CATALOG_ITEMS
        return await self.repo.update_checklist(
            checklist,
            catalog_items_count=count,
            trial_eligible=eligible,
        )

    # =========================================================================
    # Start trial (explicit + the eligibility check used by auto_start_trials)
    # =========================================================================

    async def assert_trial_eligible(self, tenant_id: UUID) -> OnboardingChecklist:
        checklist = await self.get_checklist(tenant_id)
        if checklist.catalog_items_count < TRIAL_MIN_CATALOG_ITEMS:
            raise BusinessRuleError(
                "Cannot start trial: catalog has fewer than 100 items",
                details={
                    "have": checklist.catalog_items_count,
                    "need": TRIAL_MIN_CATALOG_ITEMS,
                },
            )
        return checklist

    async def start_trial(self, *, tenant_id: UUID) -> tuple[Any, Any]:
        """Promote a setup tenant to trial: flip status, write a
        tenant_subscription row, mark the checklist with trial_started_at.
        Returns (tenant, subscription).

        Lazy import for foundation / billing keeps the load DAG acyclic.
        """
        await self.assert_trial_eligible(tenant_id)

        from app.domains.billing.repository import BillingRepository
        from app.domains.foundation.repository import FoundationRepository

        foundation_repo = FoundationRepository(self.repo.session)
        billing_repo = BillingRepository(self.repo.session)

        tenant = await foundation_repo.get_tenant(tenant_id)
        if tenant is None:
            raise NotFoundError("Tenant not found")

        now = utc_now()
        period_end = now + TRIAL_DURATION

        # Flip tenant + dates if not already in trial
        if tenant.status != "trial":
            await foundation_repo.update_tenant(
                tenant,
                status="trial",
                trial_started_at=now,
                trial_ends_at=period_end,
            )

        # Pick the default plan; bail out if it's somehow missing
        plan = await billing_repo.get_plan_by_code("aurum_pharma")
        if plan is None:
            raise BusinessRuleError("No default plan configured")

        branches_count = await foundation_repo.count_active_branches(tenant_id)
        # If no active branches yet (rare in onboarding), default to 1 so
        # the amount calculation doesn't multiply by zero.
        branches_count = max(branches_count, 1)
        amount = (plan.price_per_branch * Decimal(branches_count)).quantize(Decimal("0.01"))
        subscription = await billing_repo.insert_subscription(
            tenant_id=tenant_id,
            plan_id=plan.id,
            status="trial",
            billing_period="monthly",
            period_start=now,
            period_end=period_end,
            branches_count=branches_count,
            amount=amount,
        )

        checklist = await self.get_checklist(tenant_id)
        await self.repo.update_checklist(checklist, trial_started_at=now, trial_eligible=True)
        logger.info("trial_started_via_onboarding", tenant_id=str(tenant_id))
        return tenant, subscription
