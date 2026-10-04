"""Explicit unit prescription links; no payment, stock or clinical validation."""

from uuid import UUID

from pydantic import AwareDatetime

from app.core.errors import BusinessRuleError, NotFoundError
from app.domains.pos.models import PrescriptionLog
from app.domains.pos.schemas import PrescriptionLogCreate
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_cart_service import UnitCartService
from app.domains.pos.unit_contracts import FrozenContract
from app.domains.pos.unit_prescription_repository import UnitPrescriptionRepository


class UnitPrescriptionAttachment(FrozenContract):
    """Link identifiers only; prescription details never enter this result."""

    id: UUID
    sale_id: UUID
    unit_cart_item_id: UUID
    created_at: AwareDatetime


class UnitPrescriptionService:
    def __init__(self, repository: UnitPrescriptionRepository) -> None:
        self.repository = repository
        self.cart_repository = UnitCartRepository(repository.session)
        self.cart_service = UnitCartService(self.cart_repository)

    async def add(
        self,
        *,
        tenant_id: UUID,
        sale_id: UUID,
        unit_cart_item_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
        details: PrescriptionLogCreate,
    ) -> UnitPrescriptionAttachment:
        details = PrescriptionLogCreate.model_validate(details.model_dump())
        if details.sale_item_id is not None:
            raise BusinessRuleError("Unit prescription cannot also reference a legacy item")
        if not isinstance(unit_cart_item_id, UUID):
            raise BusinessRuleError("Unit prescription item must be a UUID")
        async with self.repository.session.begin_nested():
            sale = await self.cart_service.lock_editable_sale(
                tenant_id, sale_id, actor_id, allowed_branch_ids
            )
            await self.repository.assert_handler(tenant_id, sale.branch_id)
            item = await self.cart_repository.get_item(tenant_id, unit_cart_item_id)
            if item is None or item.sale_id != sale_id:
                raise NotFoundError("Unit cart item not found in this sale")
            catalog = await self.cart_repository.lock_catalog(tenant_id, item.catalog_id)
            if (
                catalog is None
                or not catalog.is_active
                or catalog.deleted_at is not None
                or catalog.dispensing_type != "prescription"
            ):
                raise BusinessRuleError("Unit prescription requires an active prescription item")
            row = await self.repository.add(
                tenant_id=tenant_id,
                sale_id=sale_id,
                unit_cart_item_id=unit_cart_item_id,
                actor_id=actor_id,
                details=details,
            )
            return self._attachment(row)

    async def remove(
        self,
        *,
        tenant_id: UUID,
        sale_id: UUID,
        prescription_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
    ) -> None:
        if not isinstance(prescription_id, UUID):
            raise BusinessRuleError("Prescription identifier must be a UUID")
        async with self.repository.session.begin_nested():
            sale = await self.cart_service.lock_editable_sale(
                tenant_id, sale_id, actor_id, allowed_branch_ids
            )
            await self.repository.assert_handler(tenant_id, sale.branch_id)
            row = await self.repository.get(tenant_id, sale_id, prescription_id)
            if row is None or row.unit_cart_item_id is None:
                raise NotFoundError("Unit prescription link not found in this sale")
            await self.repository.delete(row)

    async def require_complete(
        self,
        *,
        tenant_id: UUID,
        sale_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
    ) -> None:
        """Check unit references again at checkout; a cash quote cannot prove Rx coverage."""
        async with self.repository.session.begin_nested():
            await self.cart_service.lock_editable_sale(
                tenant_id, sale_id, actor_id, allowed_branch_ids
            )
            items = await self.cart_repository.list_items(tenant_id, sale_id)
            required: set[UUID] = set()
            for catalog_id in sorted({item.catalog_id for item in items}, key=str):
                catalog = await self.cart_repository.lock_catalog(tenant_id, catalog_id)
                if catalog is None or not catalog.is_active or catalog.deleted_at is not None:
                    raise BusinessRuleError("Unit cart contains an unavailable item")
                if catalog.dispensing_type == "special":
                    raise BusinessRuleError("Special-dispensing medicines are unavailable")
                if catalog.dispensing_type == "prescription":
                    required.update(item.id for item in items if item.catalog_id == catalog_id)
            if not required:
                return
            references = await self.repository.references(tenant_id, sale_id)
            if any(legacy_id is None and unit_id is None for legacy_id, unit_id in references):
                return
            covered = {unit_id for _, unit_id in references if unit_id is not None}
            if required - covered:
                raise BusinessRuleError(
                    "Prescription details are required for all Rx unit positions"
                )

    @staticmethod
    def _attachment(row: PrescriptionLog) -> UnitPrescriptionAttachment:
        if row.unit_cart_item_id is None:
            raise BusinessRuleError("Prescription has no unit cart link")
        return UnitPrescriptionAttachment(
            id=row.id,
            sale_id=row.sale_id,
            unit_cart_item_id=row.unit_cart_item_id,
            created_at=row.created_at,
        )
