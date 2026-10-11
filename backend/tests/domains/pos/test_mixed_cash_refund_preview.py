"""Mixed previews fail closed on history, cursors and current refund authority."""

from decimal import Decimal
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AurumError, BusinessRuleError, NotFoundError, PermissionDeniedError
from app.domains.foundation.models import TenantSettings
from app.domains.pos import mixed_cash_refund_preview_service as preview_module
from app.domains.pos.mixed_cash_checkout_models import MixedCashCheckout
from app.domains.pos.mixed_cash_refund_api_schemas import MixedRefundQuoteRequest
from app.domains.pos.mixed_cash_refund_preview_repository import MixedCashRefundPreviewRepository
from app.domains.pos.mixed_cash_refund_preview_service import MixedCashRefundPreviewService
from app.domains.pos.mixed_documents import MixedCheckoutResultV4, build_mixed_receipt
from app.domains.pos.models import Sale
from tests.domains.pos.test_mixed_cash_refund_api_schemas import legacy_item, physical_item
from tests.domains.pos.test_mixed_return_allocation import LEGACY_ID, PHYSICAL_ID, checkout


class PreviewRepository(MixedCashRefundPreviewRepository):
    def __init__(self, original: MixedCheckoutResultV4) -> None:
        self.session = cast(AsyncSession, object())
        receipt = build_mixed_receipt(original).model_dump(mode="json")
        self.sale = Sale(
            id=original.sale_id,
            tenant_id=original.tenant_id,
            branch_id=original.branch_id,
            register_id=original.register_id,
            shift_id=original.shift_id,
            cashier_user_id=original.cashier_id,
            sale_type="sale",
            status="completed",
            parent_sale_id=None,
            is_test=False,
            currency="TJS",
            total_amount=original.total_amount,
            created_at=original.created_at,
            completed_at=original.completed_at,
            receipt_number=original.receipt_number,
            receipt_seq=original.receipt_sequence,
            receipt_snapshot=receipt,
        )
        self.checkout = MixedCashCheckout(
            tenant_id=original.tenant_id,
            id=original.operation_id,
            sale_id=original.sale_id,
            branch_id=original.branch_id,
            register_id=original.register_id,
            shift_id=original.shift_id,
            cashier_user_id=original.cashier_id,
            cash_intent_id=original.operation_id,
            stock_command_id=original.operation_id,
            payment_id=original.operation_id,
            result_payload=original.model_dump(mode="json"),
            receipt_payload=receipt,
        )
        self.legacy: dict[UUID, Decimal] = {}
        self.physical: dict[UUID, int] = {}
        self.missing = False

    async def read(
        self, tenant_id: UUID, parent_sale_id: UUID
    ) -> tuple[Sale, MixedCashCheckout] | None:
        assert tenant_id == self.sale.tenant_id and parent_sale_id == self.sale.id
        return None if self.missing else (self.sale, self.checkout)

    async def cursors(
        self, tenant_id: UUID, parent_sale_id: UUID
    ) -> tuple[dict[UUID, Decimal], dict[UUID, int]]:
        assert tenant_id == self.sale.tenant_id and parent_sale_id == self.sale.id
        return self.legacy, self.physical


class Authority:
    def __init__(self) -> None:
        self.calls = 0
        self.denied_call: int | None = None
        self.denied_identity = False

    async def assert_identity(self, tenant_id: UUID, actor_id: UUID) -> None:
        if self.denied_identity:
            raise PermissionDeniedError("Invalid tenant identity")

    async def assert_actor(self, tenant_id: UUID, actor_id: UUID, branch_id: UUID) -> None:
        self.calls += 1
        if self.calls == self.denied_call:
            raise PermissionDeniedError("Refund permission revoked")


def setup_preview(
    monkeypatch: pytest.MonkeyPatch, *, step: int = 1
) -> tuple[PreviewRepository, Authority, MixedCashRefundPreviewService]:
    payload = checkout(step=step).model_dump(mode="python")
    operation = payload["operation_id"]
    payload.update(event_id=operation, stock_command_id=operation, is_test=False)
    payload["payments"] = [
        {"id": operation, "method": "cash", "amount": payload["total_amount"]}
    ]
    repo = PreviewRepository(MixedCheckoutResultV4.model_validate(payload))
    authority = Authority()
    monkeypatch.setattr(preview_module, "MixedReturnJournalRepository", lambda session: authority)
    return repo, authority, MixedCashRefundPreviewService(repo)


async def quote(
    service: MixedCashRefundPreviewService, repo: PreviewRepository, items: list[dict[str, object]]
) -> preview_module.MixedRefundQuote:
    return await service.quote(
        tenant_id=repo.sale.tenant_id,
        parent_sale_id=repo.sale.id,
        actor_id=UUID(int=777),
        allowed_branch_ids={repo.sale.branch_id},
        request=MixedRefundQuoteRequest.model_validate({"items": items}),
    )


class SettingsRepository:
    def __init__(self, settings: TenantSettings | None) -> None:
        self.settings = settings

    async def get_settings(self, tenant_id: UUID) -> TenantSettings | None:
        return self.settings


@pytest.mark.parametrize("fully_returned", [False, True])
async def test_options_show_remaining_historical_quantities_without_cost(
    monkeypatch: pytest.MonkeyPatch, fully_returned: bool
) -> None:
    repo, _, service = setup_preview(monkeypatch)
    repo.legacy[LEGACY_ID] = Decimal("3.000" if fully_returned else "0.001")
    repo.physical[PHYSICAL_ID] = 30 if fully_returned else 1
    settings = SettingsRepository(TenantSettings(refund_reason_mode="required_with_text"))
    monkeypatch.setattr(preview_module, "FoundationRepository", lambda session: settings)
    result = await service.options(
        tenant_id=repo.sale.tenant_id,
        parent_sale_id=repo.sale.id,
        actor_id=UUID(int=777),
        allowed_branch_ids={repo.sale.branch_id},
    )
    assert result.reason_mode == "required_with_text"
    payload = result.model_dump(mode="json")
    assert [line["line_id"] for line in payload["lines"]] == [str(LEGACY_ID), str(PHYSICAL_ID)]
    assert payload["lines"][0]["remaining_quantity"] == ("0.000" if fully_returned else "2.999")
    assert payload["lines"][1]["remaining_base_units"] == (0 if fully_returned else 29)
    assert "cost" not in result.model_dump_json()


async def test_missing_settings_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _, service = setup_preview(monkeypatch)
    settings = SettingsRepository(None)
    monkeypatch.setattr(preview_module, "FoundationRepository", lambda session: settings)
    with pytest.raises(AurumError, match="settings"):
        await service.options(
            tenant_id=repo.sale.tenant_id,
            parent_sale_id=repo.sale.id,
            actor_id=UUID(int=777),
            allowed_branch_ids={repo.sale.branch_id},
        )


async def test_quote_preserves_saved_order_offsets_and_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, authority, service = setup_preview(monkeypatch)
    repo.legacy[LEGACY_ID] = Decimal("0.001")
    repo.physical[PHYSICAL_ID] = 1
    result = await quote(service, repo, [physical_item(), legacy_item(quantity="0.999")])
    assert [line.line_id for line in result.lines] == [LEGACY_ID, PHYSICAL_ID]
    assert [line.offset for line in result.lines] == [Decimal("0.001"), 1]
    assert authority.calls == 2
    assert "cost" not in result.model_dump_json()
    first_revision = result.revision_hash
    repo.physical[PHYSICAL_ID] = 2
    changed = await quote(service, repo, [physical_item(), legacy_item(quantity="0.999")])
    assert changed.revision_hash != first_revision


async def test_quote_binds_unselected_history(monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _, service = setup_preview(monkeypatch)
    original = await quote(service, repo, [legacy_item(quantity="1.000")])
    repo.physical[PHYSICAL_ID] = 1
    changed = await quote(service, repo, [legacy_item(quantity="1.000")])
    assert changed.total_amount == original.total_amount
    assert changed.revision_hash != original.revision_hash


@pytest.mark.parametrize("failure", ["identity", "permission", "revoked", "branch", "missing"])
async def test_current_access_is_required(monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    repo, authority, service = setup_preview(monkeypatch)
    branches = {repo.sale.branch_id}
    if failure == "identity":
        authority.denied_identity = True
    elif failure in {"permission", "revoked"}:
        authority.denied_call = 1 if failure == "permission" else 2
    elif failure == "branch":
        branches = {UUID(int=999)}
    else:
        repo.missing = True
    error = NotFoundError if failure == "missing" else PermissionDeniedError
    with pytest.raises(error):
        await service.assert_access(
            tenant_id=repo.sale.tenant_id,
            parent_sale_id=repo.sale.id,
            actor_id=UUID(int=777),
            allowed_branch_ids=branches,
        )


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("sale", "status", "draft"),
        ("sale", "sale_type", "return"),
        ("sale", "is_test", True),
        ("sale", "parent_sale_id", UUID(int=999)),
        ("sale", "currency", "USD"),
        ("sale", "total_amount", Decimal("999.99")),
        ("checkout", "tenant_id", UUID(int=999)),
        ("checkout", "sale_id", UUID(int=999)),
        ("checkout", "branch_id", UUID(int=999)),
        ("checkout", "register_id", UUID(int=999)),
        ("checkout", "shift_id", UUID(int=999)),
        ("checkout", "cashier_user_id", UUID(int=999)),
        ("checkout", "cash_intent_id", UUID(int=999)),
        ("checkout", "stock_command_id", UUID(int=999)),
        ("checkout", "payment_id", UUID(int=999)),
        ("checkout", "result_payload", {}),
        ("checkout", "receipt_payload", {}),
        ("sale", "receipt_snapshot", {}),
    ],
)
async def test_inconsistent_history_fails_closed(
    monkeypatch: pytest.MonkeyPatch, target: str, field: str, value: object
) -> None:
    repo, _, service = setup_preview(monkeypatch)
    setattr(repo.sale if target == "sale" else repo.checkout, field, value)
    with pytest.raises(AurumError):
        await quote(service, repo, [legacy_item()])


@pytest.mark.parametrize(
    "kind,line_id,value",
    [
        ("legacy", LEGACY_ID, Decimal("-0.001")),
        ("legacy", LEGACY_ID, Decimal("0.0001")),
        ("legacy", LEGACY_ID, Decimal("NaN")),
        ("legacy", LEGACY_ID, Decimal("3.001")),
        ("legacy", PHYSICAL_ID, Decimal("0.001")),
        ("legacy", UUID(int=999), Decimal("0.001")),
        ("physical", PHYSICAL_ID, -1),
        ("physical", PHYSICAL_ID, True),
        ("physical", PHYSICAL_ID, 31),
        ("physical", PHYSICAL_ID, 1),
        ("physical", LEGACY_ID, 1),
        ("physical", UUID(int=999), 1),
    ],
)
async def test_all_cursors_are_checked_even_for_unselected_lines(
    monkeypatch: pytest.MonkeyPatch, kind: str, line_id: UUID, value: object
) -> None:
    repo, _, service = setup_preview(monkeypatch, step=2)
    if kind == "legacy":
        repo.legacy[line_id] = cast(Decimal, value)
    else:
        repo.physical[line_id] = cast(int, value)
    with pytest.raises(AurumError, match="quantities"):
        await quote(service, repo, [legacy_item()])


@pytest.mark.parametrize(
    "item",
    [
        legacy_item(quantity="3.001"),
        legacy_item(line_id=PHYSICAL_ID),
        physical_item(quantity=31),
        physical_item(sale_format="blister", quantity=4),
        physical_item(line_id=LEGACY_ID),
    ],
)
async def test_invalid_business_selection_is_rejected(
    monkeypatch: pytest.MonkeyPatch, item: dict[str, object]
) -> None:
    repo, _, service = setup_preview(monkeypatch)
    with pytest.raises(BusinessRuleError):
        await quote(service, repo, [item])
