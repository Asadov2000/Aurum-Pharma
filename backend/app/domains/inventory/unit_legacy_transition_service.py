"""Bind an internal transfer plan to visible accepted source history, without writes."""

from dataclasses import dataclass
from decimal import Context, localcontext
from uuid import UUID

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.inventory.unit_legacy_reconciliation import (
    LegacyReconciliationObservation,
    LegacyReconciliationSource,
)
from app.domains.inventory.unit_legacy_transition import (
    LegacyTransitionPlan,
    plan_legacy_transition,
)
from app.domains.inventory.unit_legacy_transition_repository import (
    LegacyTransitionSourceRepository,
)


@dataclass(frozen=True)
class LegacyTransitionSourcePlan:
    incoming_document_id: UUID
    incoming_item_id: UUID
    incoming_movement_id: UUID
    plan: LegacyTransitionPlan


class LegacyTransitionSourceService:
    def __init__(self, repository: LegacyTransitionSourceRepository) -> None:
        self.repository = repository

    async def build(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        batch_id: UUID,
        reconciliation_id: UUID,
        actor_id: UUID,
    ) -> LegacyTransitionSourcePlan:
        """Read a cost-sensitive plan, not conversion authorization or a reservation.

        Accepted incoming fields are immutable under their lifecycle guards.
        A writer must still recheck authorization, source and physical provenance
        under transaction locks, journal both sides atomically and preserve history.
        Read-only tenants may inspect this plan; no write eligibility is asserted.
        """
        if not all(
            isinstance(value, UUID)
            for value in (tenant_id, branch_id, batch_id, reconciliation_id, actor_id)
        ):
            raise PermissionDeniedError("Authenticated transition scope is required")
        if not await self.repository.actor_allowed(
            tenant_id=tenant_id, branch_id=branch_id, actor_id=actor_id
        ):
            raise PermissionDeniedError("Legacy transition source permission denied")
        rows = await self.repository.read_source(
            tenant_id=tenant_id,
            branch_id=branch_id,
            batch_id=batch_id,
            reconciliation_id=reconciliation_id,
            actor_id=actor_id,
        )
        if not rows:
            raise NotFoundError("Legacy transition source not found")
        if len(rows) != 1 or rows[0].source_count != 1:
            raise ConflictError("Legacy transition requires unique accepted source provenance")
        snapshot = rows[0]
        if snapshot.prepared or snapshot.current_quantity != snapshot.recorded_quantity:
            raise ConflictError("Legacy stock changed after reconciliation")
        if (
            not snapshot.provenance_matches
            or snapshot.historical_pack_cost is None
            or snapshot.current_pack_cost != snapshot.historical_pack_cost
            or snapshot.incoming_document_id is None
            or snapshot.incoming_item_id is None
            or snapshot.incoming_movement_id is None
        ):
            raise ConflictError("Legacy transition source provenance does not match")
        source = LegacyReconciliationSource(
            tenant_id=tenant_id,
            branch_id=branch_id,
            batch_id=batch_id,
            catalog_id=snapshot.catalog_id,
            legacy_quantity=snapshot.current_quantity,
        )
        observation = LegacyReconciliationObservation(
            **snapshot.count.model_dump(),
            **source.model_dump(exclude={"legacy_quantity"}),
        )
        with localcontext(Context(prec=60)):
            carried_cost = snapshot.current_quantity * snapshot.current_pack_cost
        try:
            plan = plan_legacy_transition(
                source,
                observation,
                tenant_id=tenant_id,
                branch_id=branch_id,
                reconciliation_id=reconciliation_id,
                historical_pack_cost=snapshot.historical_pack_cost,
                carried_cost=carried_cost,
            )
        except ValueError as error:
            raise BusinessRuleError(
                "Legacy transition cannot preserve exact stock and cost"
            ) from error
        return LegacyTransitionSourcePlan(
            incoming_document_id=snapshot.incoming_document_id,
            incoming_item_id=snapshot.incoming_item_id,
            incoming_movement_id=snapshot.incoming_movement_id,
            plan=plan,
        )
