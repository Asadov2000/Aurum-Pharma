"""Canonical readiness and single-use trial activation for onboarding.

Readiness is derived from the owning domain tables. The legacy wizard and
event checklist remain compatibility projections; neither can make an
unready tenant eligible. Manual and automatic activation share the same
locked, audited transition.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy.exc import DBAPIError

from app.core.errors import (
    AurumError,
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.time import utc_now
from app.domains.billing.models import TenantSubscription
from app.domains.foundation.models import Tenant
from app.domains.onboarding.models import OnboardingChecklist, TrialActivation, WizardState
from app.domains.onboarding.repository import (
    OnboardingReadinessSnapshot,
    OnboardingRepository,
)

logger = structlog.get_logger("onboarding.service")

SETUP_PHASE = timedelta(days=60)
TRIAL_DURATION = timedelta(days=14)
TRIAL_MIN_CATALOG_ITEMS = 100
ALL_STEPS = list(range(1, 9))
TAJIKISTAN_TIMEZONE = ZoneInfo("Asia/Dushanbe")


def _trial_activation_error(exc: DBAPIError) -> AurumError:
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "42501":
        return PermissionDeniedError("Trial activation is not allowed for this session")
    if sqlstate in {"22001", "22023", "23502", "23514", "P0001"}:
        return BusinessRuleError("Trial activation request is invalid")
    if sqlstate in {"23503", "23505", "40001", "40P01", "55000"}:
        return ConflictError("Launch readiness changed; refresh and try again")
    logger.error("trial_activation_database_guard_failed", sqlstate=sqlstate)
    return AurumError("Trial activation database guard failed")


@dataclass(frozen=True)
class ReadinessStepData:
    code: str
    is_complete: bool
    required: bool
    current: int | None = None
    target: int | None = None
    action_hint: str | None = None


@dataclass(frozen=True)
class ReadinessTaskData:
    code: str
    is_complete: bool


@dataclass(frozen=True)
class OnboardingOverviewData:
    tenant_id: UUID
    tenant_name: str
    tenant_status: str
    setup_ends_at: datetime
    trial_started_at: datetime | None
    trial_ends_at: datetime | None
    subscription_id: UUID | None
    steps: tuple[ReadinessStepData, ...]
    recommended_tasks: tuple[ReadinessTaskData, ...]
    required_completed: int
    required_total: int
    recommended_completed: int
    recommended_total: int
    is_ready: bool
    can_start_trial: bool
    blocker_codes: tuple[str, ...]


@dataclass(frozen=True)
class StartTrialResult:
    tenant_id: UUID
    status: str
    trial_started_at: datetime
    trial_ends_at: datetime
    subscription_id: UUID


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

    async def submit_step(
        self,
        *,
        tenant_id: UUID,
        step: int,
        data: dict[str, object],
    ) -> WizardState:
        if step not in ALL_STEPS:
            raise BusinessRuleError("Wizard step must be 1..8", details={"step": step})
        wizard = await self.repo.get_wizard_for_update(tenant_id)
        if wizard is None:
            raise NotFoundError("Wizard not initialised for this tenant")
        if wizard.is_completed:
            raise BusinessRuleError("Wizard is already completed")
        if step > wizard.current_step:
            raise BusinessRuleError(
                "Wizard steps must be completed in order",
                details={"current_step": wizard.current_step, "requested_step": step},
            )

        # The compatibility wizard must use the same canonical catalog gate.
        if step == 5:
            overview = await self.get_overview(tenant_id)
            catalog_step = next(item for item in overview.steps if item.code == "catalog")
            count = catalog_step.current or 0
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
            overview = await self.get_overview(tenant_id)
            if not overview.is_ready:
                raise BusinessRuleError(
                    "Launch checklist is not complete",
                    details={"blockers": list(overview.blocker_codes)},
                )
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
        checklist = await self.repo.get_checklist_for_update(tenant_id)
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
        checklist = await self.repo.get_checklist_for_update(tenant_id)
        if checklist is None:
            return None
        eligible = count >= TRIAL_MIN_CATALOG_ITEMS
        return await self.repo.update_checklist(
            checklist,
            catalog_items_count=count,
            trial_eligible=eligible,
        )

    # =========================================================================
    # Canonical launch readiness
    # =========================================================================

    async def _snapshot(self, tenant_id: UUID) -> OnboardingReadinessSnapshot:
        local_today = utc_now().astimezone(TAJIKISTAN_TIMEZONE).date()
        snapshot = await self.repo.get_readiness_snapshot(
            tenant_id=tenant_id,
            local_today=local_today,
        )
        if snapshot is None:
            raise NotFoundError("Tenant onboarding state not found")
        return snapshot

    @staticmethod
    def _build_overview(snapshot: OnboardingReadinessSnapshot) -> OnboardingOverviewData:
        base_steps = (
            ReadinessStepData("pharmacy_profile", snapshot.profile_complete, True),
            ReadinessStepData(
                "licensed_branch",
                snapshot.compliant_branch_count > 0,
                True,
                current=snapshot.compliant_branch_count,
                target=1,
            ),
            ReadinessStepData(
                "receipt_details",
                snapshot.receipt_ready_branch_count > 0,
                True,
                current=snapshot.receipt_ready_branch_count,
                target=1,
            ),
            ReadinessStepData(
                "tenant_owner",
                snapshot.active_owner_count > 0,
                True,
                current=snapshot.active_owner_count,
                target=1,
            ),
            ReadinessStepData(
                "catalog",
                snapshot.catalog_items_count >= TRIAL_MIN_CATALOG_ITEMS,
                True,
                current=snapshot.catalog_items_count,
                target=TRIAL_MIN_CATALOG_ITEMS,
            ),
            ReadinessStepData(
                "pos_settings",
                snapshot.operational_branch_count > 0 and bool(snapshot.payment_methods),
                True,
                current=snapshot.operational_branch_count,
                target=1,
                action_hint=(
                    "register_missing"
                    if snapshot.active_register_count == 0
                    else (
                        "payment_methods_missing"
                        if not snapshot.payment_methods
                        else "operational_branch_missing"
                    )
                ),
            ),
            ReadinessStepData("regulatory", snapshot.regulatory_complete, True),
        )
        base_ready = all(step.is_complete for step in base_steps)
        steps = (*base_steps, ReadinessStepData("ready", base_ready, True))

        tasks = (
            ReadinessTaskData(
                "catalog_loaded",
                snapshot.catalog_items_count >= TRIAL_MIN_CATALOG_ITEMS,
            ),
            ReadinessTaskData("first_incoming", snapshot.accepted_incoming_count > 0),
            ReadinessTaskData("first_sale", snapshot.completed_test_sale_count > 0),
            ReadinessTaskData("second_user", snapshot.active_membership_count >= 2),
            ReadinessTaskData("shift_opened", snapshot.opened_shift_count > 0),
            ReadinessTaskData(
                "test_receipt_printed",
                "test_receipt_printed" in snapshot.recorded_tasks,
            ),
        )
        blockers = tuple(step.code for step in base_steps if not step.is_complete)
        return OnboardingOverviewData(
            tenant_id=snapshot.tenant_id,
            tenant_name=snapshot.tenant_name,
            tenant_status=snapshot.tenant_status,
            setup_ends_at=snapshot.setup_ends_at,
            trial_started_at=snapshot.trial_started_at,
            trial_ends_at=snapshot.trial_ends_at,
            subscription_id=snapshot.subscription_id,
            steps=steps,
            recommended_tasks=tasks,
            required_completed=sum(1 for step in steps if step.is_complete),
            required_total=len(steps),
            recommended_completed=sum(1 for task in tasks if task.is_complete),
            recommended_total=len(tasks),
            is_ready=base_ready,
            can_start_trial=base_ready and snapshot.tenant_status == "setup",
            blocker_codes=blockers,
        )

    async def get_overview(self, tenant_id: UUID) -> OnboardingOverviewData:
        return self._build_overview(await self._snapshot(tenant_id))

    # =========================================================================
    # Start trial (explicit + the eligibility check used by auto_start_trials)
    # =========================================================================

    async def assert_trial_eligible(self, tenant_id: UUID) -> OnboardingOverviewData:
        overview = await self.get_overview(tenant_id)
        if not overview.is_ready:
            raise BusinessRuleError(
                "Cannot start trial: launch checklist is incomplete",
                details={"blockers": list(overview.blocker_codes)},
            )
        return overview

    async def _authorize_trial_start(
        self,
        *,
        tenant_id: UUID,
        source: Literal["manual", "automatic"],
        actor_user_id: UUID | None,
        actor_session_id: UUID | None,
    ) -> None:
        if source == "manual":
            if actor_user_id is None or actor_session_id is None:
                raise PermissionDeniedError("An authenticated owner session is required")
            if not await self.repo.has_active_ownership_for_update(
                tenant_id=tenant_id,
                user_id=actor_user_id,
            ):
                raise PermissionDeniedError("Only an active pharmacy owner can start the trial")
            return
        if actor_user_id is not None or actor_session_id is not None:
            raise BusinessRuleError("Automatic trial activation cannot impersonate a user")

    @staticmethod
    def _activation_result(tenant: Tenant, activation: TrialActivation) -> StartTrialResult:
        if tenant.status == "setup":
            raise ConflictError("Trial activation history conflicts with tenant status")
        return StartTrialResult(
            tenant_id=tenant.id,
            status=tenant.status,
            trial_started_at=activation.started_at,
            trial_ends_at=activation.trial_ends_at,
            subscription_id=activation.subscription_id,
        )

    @staticmethod
    def _legacy_trial_result(
        tenant: Tenant,
        subscription: TenantSubscription | None,
    ) -> StartTrialResult:
        if subscription is None or tenant.trial_started_at is None:
            raise BusinessRuleError(
                "Trial can only be started during setup",
                details={"tenant_status": tenant.status},
            )
        return StartTrialResult(
            tenant_id=tenant.id,
            status=tenant.status,
            trial_started_at=tenant.trial_started_at,
            trial_ends_at=tenant.trial_ends_at or subscription.period_end,
            subscription_id=subscription.id,
        )

    async def start_trial(
        self,
        *,
        tenant_id: UUID,
        source: Literal["manual", "automatic"],
        operation_id: UUID,
        actor_user_id: UUID | None = None,
        actor_session_id: UUID | None = None,
    ) -> StartTrialResult:
        """Start the tenant's single free trial in one database transaction."""
        try:
            return await self._start_trial_locked(
                tenant_id=tenant_id,
                source=source,
                operation_id=operation_id,
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
            )
        except DBAPIError as exc:
            raise _trial_activation_error(exc) from exc

    async def _start_trial_locked(
        self,
        *,
        tenant_id: UUID,
        source: Literal["manual", "automatic"],
        operation_id: UUID,
        actor_user_id: UUID | None,
        actor_session_id: UUID | None,
    ) -> StartTrialResult:
        from app.domains.billing.repository import BillingRepository
        from app.domains.foundation.repository import FoundationRepository

        foundation_repo = FoundationRepository(self.repo.session)
        billing_repo = BillingRepository(self.repo.session)

        await self.repo.acquire_trial_lock(tenant_id)
        await self._authorize_trial_start(
            tenant_id=tenant_id,
            source=source,
            actor_user_id=actor_user_id,
            actor_session_id=actor_session_id,
        )

        tenant = await self.repo.get_tenant_for_update(tenant_id)
        if tenant is None:
            raise NotFoundError("Tenant not found")

        activation = await self.repo.get_trial_activation(tenant_id)
        if activation is not None:
            return self._activation_result(tenant, activation)

        existing_subscription = await self.repo.get_current_subscription_for_update(tenant_id)
        if tenant.status != "setup":
            return self._legacy_trial_result(tenant, existing_subscription)
        if existing_subscription is not None:
            raise ConflictError("Current subscription already exists for setup tenant")

        await self.repo.lock_trial_readiness_inputs(tenant_id)
        overview = await self.assert_trial_eligible(tenant_id)

        now = utc_now()
        period_end = now + TRIAL_DURATION

        plan = await billing_repo.get_plan_by_code("aurum_pharma")
        if plan is None or not plan.is_active:
            raise BusinessRuleError("No default plan configured")

        snapshot = await self.repo.get_readiness_snapshot(
            tenant_id=tenant_id,
            local_today=utc_now().astimezone(TAJIKISTAN_TIMEZONE).date(),
        )
        if snapshot is None:
            raise NotFoundError("Tenant not found")
        branches_count = snapshot.active_branch_count
        if branches_count < 1:
            raise ConflictError("Launch readiness changed; refresh and try again")
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

        await foundation_repo.update_tenant(
            tenant,
            status="trial",
            trial_started_at=now,
            trial_ends_at=period_end,
        )

        checklist = await self.repo.get_checklist_for_update(tenant_id)
        if checklist is None:
            raise NotFoundError("Checklist not initialised for this tenant")
        await self.repo.update_checklist(
            checklist,
            catalog_items_count=(
                next(step.current for step in overview.steps if step.code == "catalog") or 0
            ),
            trial_started_at=now,
            trial_eligible=True,
        )
        wizard = await self.repo.get_wizard_for_update(tenant_id)
        if wizard is not None and not wizard.is_completed:
            await self.repo.update_wizard(
                wizard,
                current_step=8,
                steps_completed=ALL_STEPS,
                is_completed=True,
                completed_at=now,
            )
        await self.repo.record_trial_activation(
            tenant_id=tenant_id,
            operation_id=operation_id,
            source=source,
            actor_user_id=actor_user_id,
            actor_session_id=actor_session_id,
            subscription_id=subscription.id,
            started_at=now,
            trial_ends_at=period_end,
        )
        logger.info(
            "trial_started_via_onboarding",
            tenant_id=str(tenant_id),
            source=source,
        )
        return StartTrialResult(
            tenant_id=tenant.id,
            status=tenant.status,
            trial_started_at=now,
            trial_ends_at=period_end,
            subscription_id=subscription.id,
        )
