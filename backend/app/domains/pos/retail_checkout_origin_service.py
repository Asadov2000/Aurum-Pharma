"""Recover captured source identity without fabricating history or Edge authority."""

from dataclasses import dataclass
from uuid import UUID

from app.core.errors import AurumError
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.retail_checkout_origin_repository import (
    RetailCheckoutOriginRepository,
    RetailCheckoutOriginSnapshot,
)
from app.domains.pos.retail_checkout_source_repository import RetailCheckoutSourceRepository
from app.domains.pos.retail_checkout_source_service import RetailCheckoutSourceService
from app.domains.pos.retail_sale_events import RetailCheckoutResult
from app.domains.pos.unit_cart_repository import UnitCartRepository


@dataclass(frozen=True, slots=True)
class RetailCheckoutOriginEvidence:
    """A single authorized source read and its optional historical writer metadata.

    This bundle does not reserve a position or prove a transaction committed.
    The frozen container does not make nested checkout payloads deeply immutable.
    """

    result: RetailCheckoutResult
    origin: RetailCheckoutOriginSnapshot | None


class RetailCheckoutOriginService:
    def __init__(self, repository: RetailCheckoutOriginRepository) -> None:
        self.repository = repository

    async def read(
        self,
        *,
        tenant_id: UUID,
        sale_id: UUID,
        operation_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
    ) -> RetailCheckoutOriginSnapshot | None:
        evidence = await self.read_evidence(
            tenant_id=tenant_id,
            sale_id=sale_id,
            operation_id=operation_id,
            actor_id=actor_id,
            allowed_branch_ids=allowed_branch_ids,
        )
        return evidence.origin

    async def read_evidence(
        self,
        *,
        tenant_id: UUID,
        sale_id: UUID,
        operation_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
    ) -> RetailCheckoutOriginEvidence:
        """Read the caller's transaction; absence never authorizes historical backfill.

        A captured writer is not a stream position, event anchor or proof that
        the caller committed. Current access and saved checkout facts still apply.
        """
        session = self.repository.session
        with session.no_autoflush:
            result = await RetailCheckoutSourceService(
                RetailCheckoutSourceRepository(session)
            ).read(
                tenant_id=tenant_id,
                sale_id=sale_id,
                operation_id=operation_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
            )
            origin = await self.repository.read(tenant_id, sale_id, operation_id)
            expected_kind = "physical" if isinstance(result, PhysicalCheckoutResultV3) else "mixed"
            if origin is not None and (
                origin.tenant_id != result.tenant_id
                or origin.operation_id != result.operation_id
                or origin.sale_id != result.sale_id
                or origin.source_kind != expected_kind
                or origin.branch_id != result.branch_id
                or origin.register_id != result.register_id
                or origin.cashier_id != result.cashier_id
                or origin.created_by != result.cashier_id
                or type(origin.writer_epoch) is not int
                or origin.writer_epoch <= 0
            ):
                raise AurumError("Captured retail checkout origin differs from its source")
            await UnitCartRepository(session).assert_actor(tenant_id, actor_id, result.branch_id)
            return RetailCheckoutOriginEvidence(result=result, origin=origin)
