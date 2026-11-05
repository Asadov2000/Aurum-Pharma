"""Scoped recipe attachments for the unpublished unit cart."""

from uuid import UUID

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionDeniedError
from app.domains.pos.models import PrescriptionLog
from app.domains.pos.schemas import PrescriptionLogCreate


class UnitPrescriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def assert_handler(self, tenant_id: UUID, branch_id: UUID) -> None:
        allowed = await self.session.scalar(
            text(
                "SELECT public.tenant_actor_has_scoped_permission("
                ":tenant,'pos.handle_prescription',:branch)"
            ),
            {"tenant": tenant_id, "branch": branch_id},
        )
        if allowed is not True:
            raise PermissionDeniedError("Prescription handling permission required")

    async def add(
        self,
        *,
        tenant_id: UUID,
        sale_id: UUID,
        unit_cart_item_id: UUID,
        actor_id: UUID,
        details: PrescriptionLogCreate,
    ) -> PrescriptionLog:
        row = PrescriptionLog(
            tenant_id=tenant_id,
            sale_id=sale_id,
            sale_item_id=None,
            unit_cart_item_id=unit_cart_item_id,
            created_by=actor_id,
            prescription_number=details.prescription_number,
            doctor_name=details.doctor_name,
            doctor_license=details.doctor_license,
            patient_name=details.patient_name,
            notes=details.notes,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def get(
        self, tenant_id: UUID, sale_id: UUID, prescription_id: UUID
    ) -> PrescriptionLog | None:
        row: PrescriptionLog | None = await self.session.scalar(
            select(PrescriptionLog)
            .where(
                PrescriptionLog.tenant_id == tenant_id,
                PrescriptionLog.sale_id == sale_id,
                PrescriptionLog.id == prescription_id,
            )
            .execution_options(populate_existing=True)
        )
        return row

    async def references(
        self, tenant_id: UUID, sale_id: UUID
    ) -> list[tuple[UUID | None, UUID | None]]:
        result = await self.session.execute(
            select(PrescriptionLog.sale_item_id, PrescriptionLog.unit_cart_item_id).where(
                PrescriptionLog.tenant_id == tenant_id,
                PrescriptionLog.sale_id == sale_id,
                or_(
                    *(
                        func.regexp_replace(column, r"[[:space:]]", "", "g") != ""
                        for column in (
                            PrescriptionLog.prescription_number,
                            PrescriptionLog.doctor_name,
                            PrescriptionLog.doctor_license,
                            PrescriptionLog.patient_name,
                            PrescriptionLog.notes,
                        )
                    )
                ),
            )
        )
        return list(result.tuples())

    async def delete(self, row: PrescriptionLog) -> None:
        await self.session.delete(row)
        await self.session.flush()
