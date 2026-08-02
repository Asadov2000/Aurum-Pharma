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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from sqlalchemy.exc import IntegrityError, InternalError

from app.core.errors import AurumError, BusinessRuleError, NotFoundError, PermissionDeniedError
from app.core.time import utc_now
from app.domains.foundation.repository import FoundationRepository
from app.domains.inventory.expiry import ExpiryBoundaries, build_expiry_boundaries
from app.domains.inventory.models import Batch, BatchMovement, WriteOff
from app.domains.inventory.repository import (
    BatchDetailsRow,
    BatchSearchRow,
    BatchSearchSummary,
    InventoryRepository,
)

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

    async def get_batch(
        self,
        batch_id: UUID,
        *,
        tenant_id: UUID | None = None,
        allowed_branch_ids: set[UUID] | None = None,
    ) -> Batch:
        batch = await self.repo.get_batch(batch_id, tenant_id=tenant_id)
        if batch is None:
            raise NotFoundError("Batch not found")
        self._assert_branch_allowed(batch.branch_id, allowed_branch_ids=allowed_branch_ids)
        return batch

    async def get_batch_details(
        self,
        batch_id: UUID,
        *,
        tenant_id: UUID | None,
        allowed_branch_ids: set[UUID] | None = None,
        movement_limit: int = 20,
    ) -> tuple[BatchDetailsRow, str, list[BatchMovement]]:
        boundaries, timezone_name = await self._expiry_context(tenant_id)
        details = await self.repo.get_batch_details(
            batch_id,
            tenant_id=tenant_id,
            boundaries=boundaries,
        )
        if details is None:
            raise NotFoundError("Batch not found")
        self._assert_branch_allowed(
            details.batch.branch_id,
            allowed_branch_ids=allowed_branch_ids,
        )
        movements = await self.repo.list_movements(batch_id, limit=movement_limit)
        return details, timezone_name, movements

    async def list_batches(
        self,
        *,
        catalog_id: UUID | None,
        branch_id: UUID | None,
        expiry_status: str | None,
        batch_number: str | None,
        is_blocked: bool | None,
        show_empty: bool,
        page: int,
        page_size: int,
        tenant_id: UUID | None,
        branch_ids: set[UUID] | None = None,
    ) -> tuple[list[BatchSearchRow], BatchSearchSummary]:
        boundaries, _timezone_name = await self._expiry_context(tenant_id)
        return await self.repo.search_with_expiry(
            catalog_id=catalog_id,
            branch_id=branch_id,
            branch_ids=branch_ids,
            expiry_status=expiry_status,
            batch_number=batch_number,
            is_blocked=is_blocked,
            show_empty=show_empty,
            page=page,
            page_size=page_size,
            tenant_id=tenant_id,
            boundaries=boundaries,
        )

    async def list_movements(
        self,
        batch_id: UUID,
        *,
        tenant_id: UUID | None = None,
        limit: int | None = None,
        allowed_branch_ids: set[UUID] | None = None,
    ) -> list[BatchMovement]:
        await self.get_batch(
            batch_id,
            tenant_id=tenant_id,
            allowed_branch_ids=allowed_branch_ids,
        )
        return await self.repo.list_movements(batch_id, limit=limit)

    # -------------------------------------------------------------------------
    # Write-off — creates write_off row + matching batch_movement
    # -------------------------------------------------------------------------

    async def write_off(
        self,
        *,
        batch_id: UUID,
        operation_id: UUID,
        qty: Decimal,
        reason: str,
        comment: str | None,
        actor_id: UUID | None,
        tenant_id: UUID | None = None,
        allowed_branch_ids: set[UUID] | None = None,
    ) -> WriteOff:
        existing = await self.repo.get_write_off(operation_id, tenant_id=tenant_id)
        if existing is not None:
            self._assert_write_off_retry_matches(
                existing,
                batch_id=batch_id,
                qty=qty,
                reason=reason,
                comment=comment,
            )
            self._assert_branch_allowed(
                existing.branch_id,
                allowed_branch_ids=allowed_branch_ids,
            )
            return existing

        batch = await self.get_batch(
            batch_id,
            tenant_id=tenant_id,
            allowed_branch_ids=allowed_branch_ids,
        )
        if batch.is_blocked:
            raise BusinessRuleError("Cannot write off a blocked batch")
        amount = (batch.purchase_price * qty).quantize(Decimal("0.01"))

        try:
            async with self.repo.session.begin_nested():
                wo = await self.repo.insert_write_off(
                    id=operation_id,
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
        except IntegrityError:
            existing = await self.repo.get_write_off(operation_id, tenant_id=tenant_id)
            if existing is None:
                raise
            self._assert_write_off_retry_matches(
                existing,
                batch_id=batch_id,
                qty=qty,
                reason=reason,
                comment=comment,
            )
            return existing
        try:
            await self.repo.insert_movement(
                tenant_id=batch.tenant_id,
                batch_id=batch.id,
                movement_type="write_off",
                qty_delta=-qty,
                source_table="write_off",
                source_id=wo.id,
                operation_key=f"inventory:write-off:{operation_id}",
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

    @staticmethod
    def _assert_write_off_retry_matches(
        existing: WriteOff,
        *,
        batch_id: UUID,
        qty: Decimal,
        reason: str,
        comment: str | None,
    ) -> None:
        if (
            existing.batch_id != batch_id
            or existing.qty != qty
            or existing.reason != reason
            or existing.comment != comment
        ):
            raise BusinessRuleError("Write-off operation key was reused with different data")

    @staticmethod
    def _assert_branch_allowed(
        branch_id: UUID,
        *,
        allowed_branch_ids: set[UUID] | None,
    ) -> None:
        if allowed_branch_ids is not None and branch_id not in allowed_branch_ids:
            raise PermissionDeniedError("Branch access denied")

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
        settings = await FoundationRepository(self.repo.session).get_settings(tenant_id)
        if today is None:
            timezone_name = settings.report_timezone if settings is not None else "Asia/Dushanbe"
            try:
                today = utc_now().astimezone(ZoneInfo(timezone_name)).date()
            except (ValueError, ZoneInfoNotFoundError) as exc:
                raise AurumError("Tenant report timezone is invalid") from exc

        mode = settings.expired_sale_mode if settings is not None else "strict"
        include_expired = mode in ("warning", "off")
        candidates = await self.repo.fefo_candidates(
            tenant_id=tenant_id,
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

    async def _expiry_context(
        self,
        tenant_id: UUID | None,
    ) -> tuple[ExpiryBoundaries, str]:
        settings = (
            await FoundationRepository(self.repo.session).get_settings(tenant_id)
            if tenant_id is not None
            else None
        )
        timezone_name = settings.report_timezone if settings is not None else "Asia/Dushanbe"
        thresholds = settings.expiry_thresholds if settings is not None else None
        try:
            today = utc_now().astimezone(ZoneInfo(timezone_name)).date()
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise AurumError("Tenant report timezone is invalid") from exc
        return build_expiry_boundaries(today, thresholds), timezone_name
