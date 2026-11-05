"""Minimal dispensing facts for the cashier's actual unpaid cart, never authorization."""

from uuid import UUID

from app.core.errors import BusinessRuleError, NotFoundError, PermissionDeniedError
from app.domains.pos.physical_cart_schemas import PhysicalCartDispensingPreflightRead
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_cart_service import UnitCartService


class PhysicalCartDispensingPreflightService:
    def __init__(self, repository: UnitCartRepository) -> None:
        self.repository = repository

    async def read(
        self,
        *,
        tenant_id: UUID,
        sale_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
    ) -> PhysicalCartDispensingPreflightRead:
        # Do not start a savepoint: begin_nested would flush pending ORM changes.
        with self.repository.session.no_autoflush:
            sale = await UnitCartService(self.repository).lock_editable_sale(
                tenant_id, sale_id, actor_id, allowed_branch_ids
            )
            if not await self.repository.active_branch(tenant_id, sale.branch_id):
                raise PermissionDeniedError("Sale branch is inactive")
            physical = await self.repository.list_items(tenant_id, sale.id)
            legacy = await self.repository.list_legacy_items(tenant_id, sale.id)
            catalog_ids = {item.catalog_id for item in physical} | {
                item.catalog_id for item in legacy
            }
            if not catalog_ids:
                raise BusinessRuleError("Dispensing preflight requires a nonempty sale draft")
            requires_prescription = False
            for catalog_id in sorted(catalog_ids, key=str):
                catalog = await self.repository.lock_catalog(tenant_id, catalog_id)
                if catalog is None:
                    raise NotFoundError("Sale draft catalog item not found")
                if (
                    not catalog.is_active
                    or catalog.deleted_at is not None
                    or catalog.currency != "TJS"
                    or catalog.dispensing_type not in {"otc", "prescription"}
                ):
                    raise BusinessRuleError("Sale draft contains an unavailable medicine")
                requires_prescription |= catalog.dispensing_type == "prescription"
            return PhysicalCartDispensingPreflightRead(
                sale_id=sale.id, requires_prescription=requires_prescription
            )
