"""Business logic for inventory: write-off + FEFO.

Key rules:
- The DB trigger on batch_movement keeps batch.qty_remaining honest and
  refuses negative values. The service catches that exception and
  raises BusinessRuleError so the router returns a 422.
- write_off creates BOTH a write_off row AND a matching batch_movement
  with movement_type='write_off' and qty_delta=-qty.
- FEFO honours tenant_settings.expired_sale_mode:
    strict   → expired batches are excluded
    warning  → expired batches are included; service returns
               requires_warning=True if any of the chosen batches is past
               its expiry date
    off      → expired batches are included like any other
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

import structlog
from sqlalchemy.exc import IntegrityError, InternalError

from app.core.errors import BusinessRuleError, NotFoundError
from app.domains.foundation.repository import FoundationRepository
from app.domains.inventory.models import Batch, BatchMovement, WriteOff
from app.domains.inventory.repository import InventoryRepository

logger = structlog.get_logger("inventory.service")


@dataclass
class FefoPick:
    """One batch + the amount the caller should subtract from it."""

    batch: Batch
    qty: Decimal


@dataclass
class FefoSelection:
    picks: list[FefoPick]
    total_picked: Decimal
    requires_warning: bool  # True iff any pick is past expiry under 'warning' mode


class InventoryService:
    def __init__(self, repo: InventoryRepository) -> None:
        self.repo = repo

    # -------------------------------------------------------------------------
    # Batch reads
    # -------------------------------------------------------------------------

    async def get_batch(self, batch_id: UUID) -> Batch:
        batch = await self.repo.get_batch(batch_id)
        if batch is None:
            raise NotFoundError("Batch not found")
        return batch

    async def list_batches(
        self,
        *,
        catalog_id: UUID | None,
        branch_id: UUID | None,
        expiry_status: str | None,
        show_empty: bool,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, object]], int]:
        return await self.repo.search_with_expiry(
            catalog_id=catalog_id,
            branch_id=branch_id,
            expiry_status=expiry_status,
            show_empty=show_empty,
            page=page,
            page_size=page_size,
        )

    async def list_movements(
        self, batch_id: UUID, *, limit: int | None = None
    ) -> list[BatchMovement]:
        await self.get_batch(batch_id)  # 404 if missing
        return await self.repo.list_movements(batch_id, limit=limit)

    # -------------------------------------------------------------------------
    # Write-off — creates write_off row + matching batch_movement
    # -------------------------------------------------------------------------

    async def write_off(
        self,
        *,
        batch_id: UUID,
        qty: Decimal,
        reason: str,
        comment: str | None,
        actor_id: UUID | None,
    ) -> WriteOff:
        batch = await self.get_batch(batch_id)
        if batch.is_blocked:
            raise BusinessRuleError("Cannot write off a blocked batch")
        amount = (batch.purchase_price * qty).quantize(Decimal("0.01"))

        wo = await self.repo.insert_write_off(
            tenant_id=batch.tenant_id,
            branch_id=batch.branch_id,
            batch_id=batch.id,
            qty=qty,
            reason=reason,
            comment=comment,
            amount=amount,
            currency=batch.currency,
            created_by=actor_id,
        )
        try:
            await self.repo.insert_movement(
                tenant_id=batch.tenant_id,
                batch_id=batch.id,
                movement_type="write_off",
                qty_delta=-qty,
                source_table="write_off",
                source_id=wo.id,
                created_by=actor_id,
            )
        except (IntegrityError, InternalError) as exc:
            # Two layers guard qty_remaining: the trigger ("qty_remaining
            # cannot be negative") and the CHECK constraint on the column.
            # Either one means "overdraw".
            msg = str(exc).lower()
            if (
                "qty_remaining cannot be negative" in msg
                or "batch_qty_remaining_check" in msg
                or "qty_remaining_check" in msg
            ):
                raise BusinessRuleError(
                    "Write-off quantity exceeds batch remaining stock",
                    details={"requested": str(qty)},
                ) from exc
            raise
        # Trigger updated batch.qty_remaining in the DB; refresh the ORM
        # snapshot so any subsequent read in this session sees it.
        await self.repo.session.refresh(batch)
        logger.info(
            "write_off",
            batch_id=str(batch.id),
            qty=str(qty),
        )
        return wo

    # -------------------------------------------------------------------------
    # FEFO — first-expires-first-out picker used by the POS domain
    # -------------------------------------------------------------------------

    async def find_batches_fefo(
        self,
        *,
        tenant_id: UUID,
        catalog_id: UUID,
        branch_id: UUID,
        qty_needed: Decimal,
        today: date | None = None,
    ) -> FefoSelection:
        """Returns the FEFO-ordered partial selection that covers qty_needed
        (or as much as is available; the caller decides what to do with a
        short answer)."""
        if today is None:
            from app.core.time import utc_now

            today = utc_now().date()

        mode = await self._get_expired_sale_mode(tenant_id)
        include_expired = mode in ("warning", "off")
        candidates = await self.repo.fefo_candidates(
            catalog_id=catalog_id,
            branch_id=branch_id,
            include_expired=include_expired,
            today=today,
        )

        picks: list[FefoPick] = []
        remaining = qty_needed
        requires_warning = False
        for batch in candidates:
            if remaining <= 0:
                break
            take = min(batch.qty_remaining, remaining)
            if take <= 0:
                continue
            picks.append(FefoPick(batch=batch, qty=take))
            remaining -= take
            if mode == "warning" and batch.expires_at <= today:
                requires_warning = True

        total_picked = sum((p.qty for p in picks), Decimal("0"))
        return FefoSelection(
            picks=picks, total_picked=total_picked, requires_warning=requires_warning
        )

    async def _get_expired_sale_mode(self, tenant_id: UUID) -> str:
        """tenant_settings.expired_sale_mode, defaulting to 'strict' if the
        row is missing (defensive — settings are seeded with the tenant)."""
        foundation_repo = FoundationRepository(self.repo.session)
        settings = await foundation_repo.get_settings(tenant_id)
        if settings is None:
            return "strict"
        return settings.expired_sale_mode
