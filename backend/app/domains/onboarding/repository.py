"""DB access for the onboarding domain."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.onboarding.models import OnboardingChecklist, WizardState


class OnboardingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- wizard ----

    async def get_wizard(self, tenant_id: UUID) -> WizardState | None:
        return await self.session.get(WizardState, tenant_id)

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
