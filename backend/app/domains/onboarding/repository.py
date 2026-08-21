"""DB access for the onboarding domain."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.billing.models import TenantSubscription
from app.domains.foundation.models import Tenant
from app.domains.onboarding.models import OnboardingChecklist, TrialActivation, WizardState


@dataclass(frozen=True)
class OnboardingReadinessSnapshot:
    tenant_id: UUID
    tenant_name: str
    tenant_status: str
    trial_started_at: datetime | None
    trial_ends_at: datetime | None
    setup_ends_at: datetime
    profile_complete: bool
    active_branch_count: int
    compliant_branch_count: int
    receipt_ready_branch_count: int
    active_register_count: int
    operational_branch_count: int
    active_owner_count: int
    active_membership_count: int
    catalog_items_count: int
    payment_methods: tuple[str, ...]
    regulatory_complete: bool
    accepted_incoming_count: int
    completed_test_sale_count: int
    opened_shift_count: int
    recorded_tasks: frozenset[str]
    subscription_id: UUID | None


class OnboardingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- wizard ----

    async def get_wizard(self, tenant_id: UUID) -> WizardState | None:
        return await self.session.get(WizardState, tenant_id)

    async def get_wizard_for_update(self, tenant_id: UUID) -> WizardState | None:
        stmt = select(WizardState).where(WizardState.tenant_id == tenant_id).with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def insert_wizard(self, **fields: Any) -> WizardState:
        w = WizardState(**fields)
        self.session.add(w)
        await self.session.flush()
        await self.session.refresh(w)
        return w

    async def update_wizard(self, wizard: WizardState, **fields: Any) -> WizardState:
        for k, v in fields.items():
            setattr(wizard, k, v)
        await self.session.flush()
        await self.session.refresh(wizard)
        return wizard

    # ---- checklist ----

    async def get_checklist(self, tenant_id: UUID) -> OnboardingChecklist | None:
        return await self.session.get(OnboardingChecklist, tenant_id)

    async def get_checklist_for_update(self, tenant_id: UUID) -> OnboardingChecklist | None:
        stmt = (
            select(OnboardingChecklist)
            .where(OnboardingChecklist.tenant_id == tenant_id)
            .with_for_update()
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def insert_checklist(self, **fields: Any) -> OnboardingChecklist:
        c = OnboardingChecklist(**fields)
        self.session.add(c)
        await self.session.flush()
        await self.session.refresh(c)
        return c

    async def update_checklist(
        self, checklist: OnboardingChecklist, **fields: Any
    ) -> OnboardingChecklist:
        for k, v in fields.items():
            setattr(checklist, k, v)
        await self.session.flush()
        await self.session.refresh(checklist)
        return checklist

    # ---- readiness orchestration ----

    async def get_tenant_for_update(self, tenant_id: UUID) -> Tenant | None:
        stmt = (
            select(Tenant)
            .where(Tenant.id == tenant_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def acquire_trial_lock(self, tenant_id: UUID) -> None:
        await self.session.execute(
            text(
                "SELECT pg_catalog.pg_advisory_xact_lock("
                "pg_catalog.hashtextextended(CAST(:tenant_id AS TEXT), 9603))"
            ),
            {"tenant_id": str(tenant_id)},
        )

    async def lock_trial_readiness_inputs(self, tenant_id: UUID) -> None:
        """Keep every signal that made a trial eligible stable until commit."""
        lock_queries = (
            """
            SELECT branch.id
            FROM public.branch AS branch
            WHERE branch.tenant_id = :tenant_id
            FOR SHARE
            """,
            """
            SELECT register.id
            FROM public.register AS register
            WHERE register.tenant_id = :tenant_id
            FOR SHARE
            """,
            """
            SELECT catalog.id
            FROM public.tenant_catalog AS catalog
            WHERE catalog.tenant_id = :tenant_id
              AND catalog.is_active
              AND catalog.deleted_at IS NULL
            ORDER BY catalog.id
            LIMIT 100
            FOR SHARE
            """,
            """
            SELECT settings.tenant_id
            FROM public.tenant_settings AS settings
            WHERE settings.tenant_id = :tenant_id
            FOR SHARE
            """,
            """
            SELECT ownership.id
            FROM public.tenant_ownership AS ownership
            JOIN public.tenant_membership AS membership
              ON membership.id = ownership.membership_id
             AND membership.tenant_id = ownership.tenant_id
            JOIN public.app_user AS owner_user
              ON owner_user.id = membership.user_id
            WHERE ownership.tenant_id = :tenant_id
              AND ownership.is_active
              AND membership.status = 'active'
              AND owner_user.status = 'active'
            FOR SHARE OF ownership, membership, owner_user
            """,
        )
        for query in lock_queries:
            await self.session.execute(text(query), {"tenant_id": tenant_id})

    async def has_active_ownership_for_update(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
    ) -> bool:
        result = await self.session.execute(
            text("""
                SELECT ownership.id
                FROM public.tenant_ownership AS ownership
                JOIN public.tenant_membership AS membership
                  ON membership.id = ownership.membership_id
                 AND membership.tenant_id = ownership.tenant_id
                JOIN public.app_user AS owner_user
                  ON owner_user.id = membership.user_id
                WHERE ownership.tenant_id = :tenant_id
                  AND ownership.is_active
                  AND membership.user_id = :user_id
                  AND membership.status = 'active'
                  AND owner_user.status = 'active'
                LIMIT 1
                FOR SHARE OF ownership, membership, owner_user
                """),
            {"tenant_id": tenant_id, "user_id": user_id},
        )
        return result.scalar_one_or_none() is not None

    async def get_trial_activation(self, tenant_id: UUID) -> TrialActivation | None:
        return await self.session.get(TrialActivation, tenant_id)

    async def record_trial_activation(
        self,
        *,
        tenant_id: UUID,
        operation_id: UUID,
        source: str,
        actor_user_id: UUID | None,
        actor_session_id: UUID | None,
        subscription_id: UUID,
        started_at: datetime,
        trial_ends_at: datetime,
    ) -> TrialActivation:
        await self.session.execute(
            text(
                "SELECT public.record_trial_activation("
                ":tenant_id, :operation_id, :source, :actor_user_id, "
                ":actor_session_id, :subscription_id, :started_at, :trial_ends_at)"
            ),
            {
                "tenant_id": tenant_id,
                "operation_id": operation_id,
                "source": source,
                "actor_user_id": actor_user_id,
                "actor_session_id": actor_session_id,
                "subscription_id": subscription_id,
                "started_at": started_at,
                "trial_ends_at": trial_ends_at,
            },
        )
        activation = await self.get_trial_activation(tenant_id)
        if activation is None:
            raise RuntimeError("Trial activation command did not persist its result")
        return activation

    async def get_current_subscription_for_update(
        self,
        tenant_id: UUID,
    ) -> TenantSubscription | None:
        stmt = (
            select(TenantSubscription)
            .where(
                TenantSubscription.tenant_id == tenant_id,
                TenantSubscription.status.in_(("trial", "active", "grace_period", "suspended")),
            )
            .order_by(TenantSubscription.period_start.desc())
            .limit(1)
            .with_for_update()
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_readiness_snapshot(
        self,
        *,
        tenant_id: UUID,
        local_today: date,
    ) -> OnboardingReadinessSnapshot | None:
        """Read every launch signal from its canonical domain in one round trip."""
        result = await self.session.execute(
            text("""
                SELECT
                  tenant.id AS tenant_id,
                  tenant.name AS tenant_name,
                  tenant.status AS tenant_status,
                  tenant.trial_started_at,
                  tenant.trial_ends_at,
                  COALESCE(
                    checklist.setup_ends_at,
                    tenant.setup_started_at + INTERVAL '60 days'
                  ) AS setup_ends_at,
                  (
                    BTRIM(tenant.name) <> ''
                    AND BTRIM(tenant.contact_email) <> ''
                  ) AS profile_complete,
                  (
                    SELECT COUNT(*)
                    FROM public.branch AS branch
                    WHERE branch.tenant_id = tenant.id
                      AND branch.is_active
                  ) AS active_branch_count,
                  (
                    SELECT COUNT(*)
                    FROM public.branch AS branch
                    WHERE branch.tenant_id = tenant.id
                      AND branch.is_active
                      AND NULLIF(BTRIM(branch.address), '') IS NOT NULL
                      AND NULLIF(BTRIM(branch.license_number), '') IS NOT NULL
                      AND branch.license_expires_at >= :local_today
                  ) AS compliant_branch_count,
                  (
                    SELECT COUNT(*)
                    FROM public.branch AS branch
                    WHERE branch.tenant_id = tenant.id
                      AND branch.is_active
                      AND NULLIF(BTRIM(branch.address), '') IS NOT NULL
                      AND NULLIF(BTRIM(branch.license_number), '') IS NOT NULL
                      AND branch.license_expires_at >= :local_today
                      AND pg_catalog.jsonb_typeof(branch.receipt_header) = 'object'
                      AND NULLIF(BTRIM(branch.receipt_header ->> 'line1'), '') IS NOT NULL
                  ) AS receipt_ready_branch_count,
                  (
                    SELECT COUNT(*)
                    FROM public.register AS register
                    JOIN public.branch AS branch
                      ON branch.id = register.branch_id
                     AND branch.tenant_id = register.tenant_id
                    WHERE register.tenant_id = tenant.id
                      AND register.is_active
                      AND branch.is_active
                  ) AS active_register_count,
                  (
                    SELECT COUNT(*)
                    FROM public.branch AS branch
                    WHERE branch.tenant_id = tenant.id
                      AND branch.is_active
                      AND NULLIF(BTRIM(branch.address), '') IS NOT NULL
                      AND NULLIF(BTRIM(branch.license_number), '') IS NOT NULL
                      AND branch.license_expires_at >= :local_today
                      AND pg_catalog.jsonb_typeof(branch.receipt_header) = 'object'
                      AND NULLIF(BTRIM(branch.receipt_header ->> 'line1'), '') IS NOT NULL
                      AND EXISTS (
                        SELECT 1
                        FROM public.register AS register
                        WHERE register.tenant_id = tenant.id
                          AND register.branch_id = branch.id
                          AND register.is_active
                      )
                  ) AS operational_branch_count,
                  (
                    SELECT COUNT(*)
                    FROM public.tenant_ownership AS ownership
                    JOIN public.tenant_membership AS membership
                      ON membership.id = ownership.membership_id
                     AND membership.tenant_id = ownership.tenant_id
                    JOIN public.app_user AS owner_user
                      ON owner_user.id = membership.user_id
                    WHERE ownership.tenant_id = tenant.id
                      AND ownership.is_active
                      AND membership.status = 'active'
                      AND owner_user.status = 'active'
                  ) AS active_owner_count,
                  (
                    SELECT COUNT(*)
                    FROM public.tenant_membership AS membership
                    WHERE membership.tenant_id = tenant.id
                      AND membership.status = 'active'
                  ) AS active_membership_count,
                  (
                    SELECT COUNT(*)
                    FROM public.tenant_catalog AS catalog
                    WHERE catalog.tenant_id = tenant.id
                      AND catalog.is_active
                      AND catalog.deleted_at IS NULL
                  ) AS catalog_items_count,
                  COALESCE(settings.pos_payment_methods, '[]'::jsonb) AS payment_methods,
                  (
                    NULLIF(BTRIM(settings.prescription_warning_text), '') IS NOT NULL
                    AND settings.expired_sale_mode IN ('strict', 'warning', 'off')
                    AND settings.refund_reason_mode IN (
                      'required', 'required_with_text', 'optional', 'off'
                    )
                  ) AS regulatory_complete,
                  (
                    SELECT COUNT(*)
                    FROM public.incoming_document AS incoming
                    WHERE incoming.tenant_id = tenant.id
                      AND incoming.status = 'accepted'
                  ) AS accepted_incoming_count,
                  (
                    SELECT COUNT(*)
                    FROM public.sale AS sale
                    WHERE sale.tenant_id = tenant.id
                      AND sale.status = 'completed'
                      AND sale.sale_type = 'sale'
                      AND sale.is_test
                  ) AS completed_test_sale_count,
                  (
                    SELECT COUNT(*)
                    FROM public.shift AS shift
                    WHERE shift.tenant_id = tenant.id
                  ) AS opened_shift_count,
                  COALESCE(checklist.completed_tasks, '[]'::jsonb) AS recorded_tasks,
                  subscription.id AS subscription_id
                FROM public.tenant AS tenant
                LEFT JOIN public.tenant_settings AS settings
                  ON settings.tenant_id = tenant.id
                LEFT JOIN public.onboarding_checklist AS checklist
                  ON checklist.tenant_id = tenant.id
                LEFT JOIN LATERAL (
                  SELECT current_subscription.id
                  FROM public.tenant_subscription AS current_subscription
                  WHERE current_subscription.tenant_id = tenant.id
                    AND current_subscription.status IN (
                      'trial', 'active', 'grace_period', 'suspended'
                    )
                  ORDER BY current_subscription.period_start DESC
                  LIMIT 1
                ) AS subscription ON TRUE
                WHERE tenant.id = :tenant_id
                """),
            {"tenant_id": tenant_id, "local_today": local_today},
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        return OnboardingReadinessSnapshot(
            tenant_id=row["tenant_id"],
            tenant_name=str(row["tenant_name"]),
            tenant_status=str(row["tenant_status"]),
            trial_started_at=row["trial_started_at"],
            trial_ends_at=row["trial_ends_at"],
            setup_ends_at=row["setup_ends_at"],
            profile_complete=bool(row["profile_complete"]),
            active_branch_count=int(row["active_branch_count"]),
            compliant_branch_count=int(row["compliant_branch_count"]),
            receipt_ready_branch_count=int(row["receipt_ready_branch_count"]),
            active_register_count=int(row["active_register_count"]),
            operational_branch_count=int(row["operational_branch_count"]),
            active_owner_count=int(row["active_owner_count"]),
            active_membership_count=int(row["active_membership_count"]),
            catalog_items_count=int(row["catalog_items_count"]),
            payment_methods=tuple(row["payment_methods"]),
            regulatory_complete=bool(row["regulatory_complete"]),
            accepted_incoming_count=int(row["accepted_incoming_count"]),
            completed_test_sale_count=int(row["completed_test_sale_count"]),
            opened_shift_count=int(row["opened_shift_count"]),
            recorded_tasks=frozenset(row["recorded_tasks"]),
            subscription_id=row["subscription_id"],
        )
