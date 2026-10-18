"""Saved reviews recover immutable allocations without issuing another quote."""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AurumError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.pos.physical_return_journal_repository import PhysicalReturnJournalRepository
from app.domains.pos.unit_electronic_refund_intent_models import UnitElectronicRefundIntent
from app.domains.pos.unit_electronic_refund_intent_repository import (
    UnitElectronicRefundIntentRepository,
)
from app.domains.pos.unit_electronic_refund_intent_schemas import SaveElectronicRefundIntentRequest
from app.domains.pos.unit_electronic_refund_intent_service import (
    UnitElectronicRefundIntentService,
)
from app.domains.pos.unit_electronic_refund_preview import UnitElectronicRefundPreview
from app.domains.pos.unit_electronic_refund_preview_service import (
    UnitElectronicRefundPreviewService,
)


class Session:
    def __init__(self) -> None:
        self.new: set[object] = set()
        self.dirty: set[object] = set()
        self.deleted: set[object] = set()
        self.autoflush_disabled = False

    @asynccontextmanager
    async def begin_nested(self) -> AsyncIterator[None]:
        yield

    @property
    @contextmanager
    def no_autoflush(self) -> Iterator[None]:
        self.autoflush_disabled = True
        try:
            yield
        finally:
            self.autoflush_disabled = False


class Review:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tenant, self.actor, self.branch, self.parent, line = (uuid4() for _ in range(5))
        self.request = SaveElectronicRefundIntentRequest.model_validate(
            {
                "operation_id": uuid4(),
                "request": {
                    "parent_sale_id": self.parent,
                    "selection": {
                        "items": [{"line_id": line, "sale_format": "unit", "quantity": 1}]
                    },
                    "expected_total_amount": "0.99",
                    "expected_revision_hash": "a" * 64,
                },
            }
        )
        self.preview = UnitElectronicRefundPreview.model_validate(
            {
                "parent_sale_id": self.parent,
                "branch_id": self.branch,
                "original_receipt_number": "TEST-1",
                "revision_hash": "a" * 64,
                "total_amount": "0.99",
                "lines": [
                    {
                        "line_id": line,
                        "sale_format": "unit",
                        "quantity": 1,
                        "base_units": 1,
                        "offset": 0,
                        "gross_amount": "1.00",
                        "discount_amount": "0.01",
                        "net_amount": "0.99",
                    }
                ],
                "payments": [{"original_payment_id": uuid4(), "method": "card", "amount": "0.99"}],
            }
        )
        self.session = Session()
        self.service = UnitElectronicRefundIntentService(
            UnitElectronicRefundIntentRepository(cast(AsyncSession, self.session))
        )
        self.get = AsyncMock(return_value=None)
        self.insert = AsyncMock(side_effect=self.store)
        self.quote = AsyncMock(return_value=self.preview)
        self.authority = AsyncMock()
        monkeypatch.setattr(UnitElectronicRefundIntentRepository, "get", self.get)
        monkeypatch.setattr(UnitElectronicRefundIntentRepository, "insert", self.insert)
        monkeypatch.setattr(UnitElectronicRefundIntentRepository, "lock_operation", AsyncMock())
        monkeypatch.setattr(UnitElectronicRefundPreviewService, "quote", self.quote)
        monkeypatch.setattr(PhysicalReturnJournalRepository, "assert_actor", self.authority)

    async def store(self, intent: UnitElectronicRefundIntent) -> None:
        intent.created_at = datetime(2026, 1, 1, tzinfo=UTC)
        self.get.return_value = intent

    async def save(self) -> object:
        return await self.service.save(
            tenant_id=self.tenant,
            actor_id=self.actor,
            allowed_branch_ids={self.branch},
            request=self.request,
        )

    async def read(self) -> object:
        return await self.service.read(
            tenant_id=self.tenant,
            actor_id=self.actor,
            allowed_branch_ids={self.branch},
            parent_sale_id=self.parent,
            operation_id=self.request.operation_id,
        )


@pytest.fixture
def review(monkeypatch: pytest.MonkeyPatch) -> Review:
    return Review(monkeypatch)


async def test_save_and_replay_preserve_allocation_and_timestamp(review: Review) -> None:
    saved = await review.save()
    assert await review.save() == saved
    assert await review.read() == saved
    review.quote.assert_awaited_once()
    review.insert.assert_awaited_once()
    review.get.assert_awaited_with(review.tenant, review.request.operation_id)


async def test_reused_uuid_with_changed_request_is_conflict(review: Review) -> None:
    await review.save()
    review.request = review.request.model_copy(
        update={
            "request": review.request.request.model_copy(
                update={"expected_total_amount": Decimal("1.00")}
            )
        }
    )
    with pytest.raises(ConflictError):
        await review.save()
    assert review.insert.await_count == review.quote.await_count == 1


@pytest.mark.parametrize("field", ["expected_total_amount", "expected_revision_hash"])
async def test_stale_review_is_not_saved(review: Review, field: str) -> None:
    value = Decimal("1.00") if field == "expected_total_amount" else "b" * 64
    review.request = review.request.model_copy(
        update={"request": review.request.request.model_copy(update={field: value})}
    )
    with pytest.raises(ConflictError):
        await review.save()
    review.insert.assert_not_awaited()


@pytest.mark.parametrize("pending", ["new", "dirty", "deleted"])
async def test_save_rejects_pending_orm_work(review: Review, pending: str) -> None:
    getattr(review.session, pending).add(object())
    with pytest.raises(ConflictError):
        await review.save()
    review.authority.assert_not_awaited()
    review.quote.assert_not_awaited()
    review.insert.assert_not_awaited()


async def test_read_disables_autoflush_even_with_pending_changes(review: Review) -> None:
    await review.save()
    review.session.dirty.add(object())

    async def guarded_get(*args: object) -> UnitElectronicRefundIntent:
        assert review.session.autoflush_disabled
        return cast(UnitElectronicRefundIntent, review.get.return_value)

    review.get.side_effect = guarded_get
    await review.read()
    assert not review.session.autoflush_disabled
    assert review.insert.await_count == review.quote.await_count == 1


@pytest.mark.parametrize("scope", ["actor", "branch", "parent", "tenant"])
async def test_read_enforces_scope(review: Review, scope: str) -> None:
    await review.save()
    if scope == "tenant":
        review.tenant = uuid4()
        review.get.return_value = None  # Repository tenant predicate hides the other tenant.
    else:
        setattr(review, scope, uuid4())
    error = NotFoundError if scope in {"tenant", "parent"} else PermissionDeniedError
    with pytest.raises(error):
        await review.read()
    review.get.assert_awaited_with(review.tenant, review.request.operation_id)


async def test_read_rechecks_current_authority(review: Review) -> None:
    await review.save()
    review.authority.side_effect = PermissionDeniedError("revoked")
    with pytest.raises(PermissionDeniedError):
        await review.read()


@pytest.mark.parametrize("corruption", ["request", "result", "hash", "scope", "sum"])
async def test_corrupt_snapshot_is_rejected(review: Review, corruption: str) -> None:
    await review.save()
    intent = review.get.return_value
    if corruption == "request":
        intent.request_payload = {}
    elif corruption == "result":
        intent.result_payload = {}
    elif corruption == "hash":
        intent.request_hash = "0" * 64
    elif corruption == "scope":
        intent.branch_id = uuid4()
    else:
        intent.result_payload = {
            **intent.result_payload,
            "payments": [{**intent.result_payload["payments"][0], "amount": "0.98"}],
        }
    with pytest.raises(AurumError):
        review.service._result(intent)
    assert review.insert.await_count == 1
