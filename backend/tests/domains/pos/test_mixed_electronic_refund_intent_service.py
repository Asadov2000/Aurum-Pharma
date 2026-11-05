"""Saved reviews recover immutable allocations without issuing another quote."""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AurumError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.pos.mixed_electronic_refund_intent_models import MixedElectronicRefundIntent
from app.domains.pos.mixed_electronic_refund_intent_repository import (
    MixedElectronicRefundIntentRepository,
)
from app.domains.pos.mixed_electronic_refund_intent_schemas import (
    SaveMixedElectronicRefundIntentRequest,
)
from app.domains.pos.mixed_electronic_refund_intent_service import (
    MixedElectronicRefundIntentService,
)
from app.domains.pos.mixed_electronic_refund_preview import MixedElectronicRefundPreview
from app.domains.pos.mixed_electronic_refund_preview_service import (
    MixedElectronicRefundPreviewService,
)
from app.domains.pos.mixed_return_journal_repository import MixedReturnJournalRepository
from app.domains.pos.unit_cart_repository import UnitCartRepository


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
        self.request = SaveMixedElectronicRefundIntentRequest.model_validate(
            {
                "operation_id": uuid4(),
                "request": {
                    "parent_sale_id": self.parent,
                    "selection": {
                        "items": [
                            {
                                "inventory_kind": "physical_unit",
                                "line_id": line,
                                "sale_format": "unit",
                                "quantity": 1,
                            }
                        ]
                    },
                    "expected_total_amount": "0.99",
                    "expected_revision_hash": "a" * 64,
                },
            }
        )
        self.preview = MixedElectronicRefundPreview.model_validate(
            {
                "parent_sale_id": self.parent,
                "branch_id": self.branch,
                "original_receipt_number": "TEST-1",
                "revision_hash": "a" * 64,
                "total_amount": "0.99",
                "lines": [
                    {
                        "inventory_kind": "physical_unit",
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
        self.service = MixedElectronicRefundIntentService(
            MixedElectronicRefundIntentRepository(cast(AsyncSession, self.session))
        )
        monkeypatch.setattr(
            MixedElectronicRefundIntentRepository, "is_read_committed", AsyncMock(return_value=True)
        )
        monkeypatch.setattr(
            MixedReturnJournalRepository,
            "lock_sale",
            AsyncMock(return_value=type("Parent", (), {"branch_id": self.branch})()),
        )
        self.get = AsyncMock(return_value=None)
        self.insert = AsyncMock(side_effect=self.store)
        self.quote = AsyncMock(return_value=self.preview)
        self.identity = AsyncMock()
        self.authority = AsyncMock()
        monkeypatch.setattr(MixedElectronicRefundIntentRepository, "get", self.get)
        monkeypatch.setattr(MixedElectronicRefundIntentRepository, "insert", self.insert)
        monkeypatch.setattr(MixedElectronicRefundIntentRepository, "lock_operation", AsyncMock())
        monkeypatch.setattr(MixedElectronicRefundPreviewService, "quote", self.quote)
        monkeypatch.setattr(UnitCartRepository, "assert_actor", self.identity)
        monkeypatch.setattr(MixedReturnJournalRepository, "assert_actor", self.authority)

    async def store(self, intent: MixedElectronicRefundIntent) -> None:
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

    async def guarded_get(*args: object) -> MixedElectronicRefundIntent:
        assert review.session.autoflush_disabled
        return cast(MixedElectronicRefundIntent, review.get.return_value)

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


async def test_branch_refunder_does_not_need_tenant_wide_refund_access(review: Review) -> None:
    async def branch_authority(tenant: UUID, actor: UUID, branch: UUID | None) -> None:
        if (tenant, actor, branch) != (review.tenant, review.actor, review.branch):
            raise PermissionDeniedError("Branch refund access denied")

    review.authority.side_effect = branch_authority
    saved = await review.save()
    assert await review.save() == saved
    assert await review.read() == saved
    assert review.identity.await_count == 3
    review.identity.assert_awaited_with(review.tenant, review.actor)


async def test_identity_denial_prevents_reading_saved_reviews(review: Review) -> None:
    await review.save()
    review.identity.side_effect = PermissionDeniedError("Invalid identity")
    review.get.reset_mock()
    review.authority.reset_mock()
    with pytest.raises(PermissionDeniedError):
        await review.read()
    review.get.assert_not_awaited()
    review.authority.assert_not_awaited()


async def test_fractional_legacy_and_physical_review_recover_exactly(review: Review) -> None:
    legacy = uuid4()
    request = review.request.model_dump(mode="json")
    request["request"]["selection"]["items"].append(
        {"inventory_kind": "legacy_pack", "line_id": str(legacy), "quantity": "0.250"}
    )
    request["request"]["expected_total_amount"] = "2.00"
    review.request = SaveMixedElectronicRefundIntentRequest.model_validate(request)
    preview = review.preview.model_dump(mode="json")
    preview["total_amount"] = "2.00"
    preview["payments"][0]["amount"] = "2.00"
    preview["lines"].append(
        {
            "inventory_kind": "legacy_pack",
            "line_id": str(legacy),
            "quantity": "0.250",
            "offset": "0.125",
            "gross_amount": "1.01",
            "discount_amount": "0.00",
            "net_amount": "1.01",
        }
    )
    review.preview = MixedElectronicRefundPreview.model_validate(preview)
    review.quote.return_value = review.preview
    saved = await review.save()
    assert await review.save() == saved
    assert await review.read() == saved
    review.quote.assert_awaited_once()
    assert {line.inventory_kind for line in review.preview.lines} == {
        "legacy_pack",
        "physical_unit",
    }


async def test_changed_inventory_kind_in_stored_selection_is_rejected(review: Review) -> None:
    await review.save()
    intent = review.get.return_value
    payload = dict(intent.result_payload)
    line = dict(payload["lines"][0])
    line.pop("sale_format")
    line.pop("base_units")
    line.update(inventory_kind="legacy_pack", quantity="1.000", offset="0.000")
    payload["lines"] = [line]
    intent.result_payload = payload
    with pytest.raises(AurumError):
        await review.read()
