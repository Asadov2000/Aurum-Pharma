"""Read synthetic V4 history without financial writes; mocks do not prove SQL RLS."""

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import AurumError, BusinessRuleError, NotFoundError, PermissionDeniedError
from app.domains.pos.mixed_documents import MixedCheckoutResultV4, build_mixed_receipt
from app.domains.pos.mixed_receipt_read import mixed_receipt_lines, mixed_receipt_snapshot
from app.domains.pos.models import Sale, SaleItem, SalePayment
from app.domains.pos.receipt_pdf import _write_receipt_summary, render_receipt_pdf
from app.domains.pos.repository import POSRepository
from app.domains.pos.router import get_receipt, get_sale
from app.domains.pos.service import POSService
from tests.domains.pos.test_mixed_documents import legacy, line, mixed_payload


def synthetic_sale(**changes: object) -> Sale:
    """An unattached ORM object, never inserted or used to bypass immutable guards."""
    result = MixedCheckoutResultV4.model_validate(mixed_payload())
    fields: dict[str, object] = {
        "id": result.sale_id,
        "tenant_id": result.tenant_id,
        "branch_id": result.branch_id,
        "register_id": result.register_id,
        "shift_id": result.shift_id,
        "cashier_user_id": result.cashier_id,
        "operation_id": result.operation_id,
        "created_at": result.created_at,
        "completed_at": result.completed_at,
        "sale_type": "sale",
        "status": "completed",
        "parent_sale_id": None,
        "refund_attempt_id": None,
        "voided_at": None,
        "voided_by_sale_id": None,
        "receipt_number": result.receipt_number,
        "receipt_seq": result.receipt_sequence,
        "is_test": result.is_test,
        "total_amount": result.total_amount,
        "currency": result.currency,
        "receipt_snapshot": build_mixed_receipt(result).model_dump(mode="json"),
    }
    fields.update(changes)
    return Sale(**fields)


def controlled_service(sale: Sale, monkeypatch: pytest.MonkeyPatch) -> tuple[POSService, MagicMock]:
    """Exercise real service authorization/dispatch against a strictly controlled repository."""
    repository = MagicMock(spec=POSRepository)
    repository.get_sale.return_value = sale
    repository.session = MagicMock(spec=AsyncSession)

    async def metadata(model: type[object], identifier: UUID) -> object:
        if model.__name__ == "Tenant":
            return SimpleNamespace(name="Синтетическая аптека")
        assert model.__name__ == "Branch" and identifier == sale.branch_id
        return SimpleNamespace(name="Филиал", address=None, license_number=None)

    repository.session.get.side_effect = metadata
    repository.get_user_display_name.return_value = "Кассир"
    repository.sale_lifecycle.return_value = SimpleNamespace(
        status="completed", voided_at=None, voided_by_sale_id=None
    )
    repository.refunded_quantities.return_value = {}
    repository.list_items_with_batch.return_value = [
        (
            SaleItem(
                id=UUID(int=40),
                tenant_id=sale.tenant_id,
                sale_id=sale.id,
                catalog_id=UUID(int=41),
                batch_id=UUID(int=42),
                qty=Decimal("0.001"),
                unit_price=Decimal("25.00"),
                total_price=Decimal("0.02"),
                discount_amount=Decimal("0.00"),
                position=99,
                currency="TJS",
            ),
            "SYNTHETIC-BATCH",
            None,
            None,
            "Сегодняшнее имя не из чека",
        )
    ]
    repository.list_payments.return_value = [
        SalePayment(
            id=UUID(int=22),
            sale_id=sale.id,
            tenant_id=sale.tenant_id,
            operation_id=sale.operation_id,
            payment_method="cash",
            amount=sale.total_amount,
            currency="TJS",
        )
    ]
    repository.list_prepared_cart_items.side_effect = AssertionError("V4 has no prepared cart")
    service = POSService(repository)
    monkeypatch.setattr(service, "_report_tz", AsyncMock(return_value="Asia/Dushanbe"))
    return service, repository


def test_saved_mixed_snapshot_and_adapter_keep_original_retail_facts() -> None:
    sale = synthetic_sale()
    before = deepcopy(sale.receipt_snapshot)
    snapshot = mixed_receipt_snapshot(sale)
    assert snapshot is not None
    items = mixed_receipt_lines(snapshot)
    assert [item.position for item in items] == [1, 2]
    assert [item.qty for item in items] == [Decimal("0.001"), Decimal("2")]
    assert [item.unit_label for item in items] == ["уп.", "блист."]
    assert items[0].quantity_description is None
    assert items[1].quantity_description == "В блистере: 10 табл."
    assert sum(item.total_price for item in items) == sale.total_amount
    assert sum(item.discount_amount for item in items) == Decimal("1.50")
    assert sale.receipt_snapshot == before


@pytest.mark.parametrize(
    "sale_format,base_units,label,description",
    [
        ("pack", 60, "уп.", "В упаковке: 30 табл."),
        ("blister", 20, "блист.", "В блистере: 10 табл."),
        ("unit", 2, "табл.", None),
    ],
)
def test_mixed_adapter_preserves_global_interleaving_and_physical_formats(
    sale_format: str, base_units: int, label: str, description: str | None
) -> None:
    result = MixedCheckoutResultV4.model_validate(
        mixed_payload(
            lines=[
                line(id=UUID(int=20), position=1, catalog_id=UUID(int=21)),
                legacy(position=2),
                line(
                    id=UUID(int=23),
                    position=3,
                    catalog_id=UUID(int=21),
                    sale_format=sale_format,
                    base_units=base_units,
                ),
            ],
            total_amount="47.02",
            payments=[{"id": UUID(int=22), "method": "cash", "amount": "47.02"}],
        )
    )
    items = mixed_receipt_lines(build_mixed_receipt(result))
    assert [item.position for item in items] == [1, 2, 3]
    assert items[1].qty == Decimal("0.001") and items[1].unit_label == "уп."
    assert items[2].qty == 2
    assert items[2].unit_label == label
    assert items[2].quantity_description == description
    assert items[2].unit_price == result.lines[2].original_retail_unit_price
    assert sum(item.total_price for item in items) == result.total_amount


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "draft"),
        ("sale_type", "return"),
        ("receipt_number", "Other receipt"),
        ("receipt_seq", 999),
        ("completed_at", None),
        ("is_test", False),
        ("currency", "USD"),
        ("total_amount", Decimal("23.51")),
    ],
)
def test_mixed_snapshot_is_bound_to_matching_completed_sale(field: str, value: object) -> None:
    sale = synthetic_sale(**{field: value})
    with pytest.raises(AurumError):
        mixed_receipt_snapshot(sale)


@pytest.mark.parametrize("version", [True, "4", 3, 5, None])
def test_mixed_document_kind_requires_exact_supported_version(version: object) -> None:
    sale = synthetic_sale()
    assert sale.receipt_snapshot is not None
    payload = deepcopy(sale.receipt_snapshot)
    payload["schema_version"] = version
    sale.receipt_snapshot = payload
    with pytest.raises(AurumError):
        mixed_receipt_snapshot(sale)


@pytest.mark.parametrize(
    "payload",
    [None, {}, {"schema_version": 3, "document_kind": "physical_receipt"}, {"sale_id": "legacy"}],
)
def test_other_snapshot_domains_are_left_for_their_own_readers(payload: object) -> None:
    assert mixed_receipt_snapshot(synthetic_sale(receipt_snapshot=payload)) is None


@pytest.mark.parametrize("field", ["cost_amount", "purchase_price", "prescription"])
def test_mixed_snapshot_rejects_sensitive_extra_fields(field: str) -> None:
    for line_level in (False, True):
        sale = synthetic_sale()
        payload = deepcopy(sale.receipt_snapshot)
        assert payload is not None
        target = payload["lines"][0] if line_level else payload
        target[field] = "forbidden"
        sale.receipt_snapshot = payload
        with pytest.raises(AurumError):
            mixed_receipt_snapshot(sale)


async def test_authorized_service_reads_and_prints_synthetic_history_without_write_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sale = synthetic_sale()
    service, repository = controlled_service(sale, monkeypatch)
    before = deepcopy(sale.receipt_snapshot)
    receipt = await service.build_receipt(
        sale.id, viewer_id=sale.cashier_user_id, allowed_branch_ids={sale.branch_id}
    )
    assert receipt.items == service.get_mixed_receipt_items(sale)
    assert receipt.items[0].name == "Сохранённое обычное лекарство"
    assert receipt.items[1].name == "Лекарство 500 мг"
    assert receipt.total == sale.total_amount == receipt.payments[0].amount
    assert receipt.datetime is not None and receipt.datetime.utcoffset() == timedelta(hours=5)
    assert receipt.paid_total is None and receipt.change is None
    assert render_receipt_pdf(receipt).startswith(b"%PDF-")
    summary: list[str] = []

    def record(
        txt: str, *, h: float = 5, size: int = 9, bold: bool = False, align: str = "L"
    ) -> None:
        summary.append(txt)

    _write_receipt_summary(receipt, record)
    assert not any(value.startswith(("Принято:", "Сдача:")) for value in summary)
    assert await service.build_receipt(sale.id) == receipt
    assert sale.receipt_snapshot == before
    repository.session.add.assert_not_called()
    repository.session.flush.assert_not_awaited()
    repository.session.commit.assert_not_awaited()
    repository.session.execute.assert_not_awaited()
    repository.list_items.assert_not_awaited()


@pytest.mark.parametrize("denial", ["other_cashier", "wrong_branch", "missing_sale"])
async def test_service_authorization_precedes_mixed_snapshot_parsing(
    monkeypatch: pytest.MonkeyPatch, denial: str
) -> None:
    sale = synthetic_sale()
    service, repository = controlled_service(sale, monkeypatch)
    parser = MagicMock(side_effect=AssertionError("Forbidden snapshots must not be parsed"))
    monkeypatch.setattr("app.domains.pos.service.mixed_receipt_snapshot", parser)
    if denial == "missing_sale":
        repository.get_sale.return_value = None
    with pytest.raises((PermissionDeniedError, NotFoundError)):
        await service.build_receipt(
            sale.id,
            viewer_id=uuid4() if denial == "other_cashier" else sale.cashier_user_id,
            allowed_branch_ids={uuid4() if denial == "wrong_branch" else sale.branch_id},
        )
    parser.assert_not_called()
    repository.session.get.assert_not_awaited()


async def test_branch_viewer_reads_synthetic_history_without_financial_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sale = synthetic_sale()
    service, _repository = controlled_service(sale, monkeypatch)
    viewer = uuid4()
    receipt = await service.build_receipt(
        sale.id,
        viewer_id=viewer,
        allowed_branch_ids={sale.branch_id},
        allowed_view_branch_ids={sale.branch_id},
    )
    assert receipt.total == sale.total_amount
    with pytest.raises(PermissionDeniedError):
        await service.build_receipt(sale.id, viewer_id=viewer, allowed_branch_ids={sale.branch_id})


async def test_router_returns_mixed_rows_once_without_legacy_position_join_or_prepared_cart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sale = synthetic_sale()
    service, repository = controlled_service(sale, monkeypatch)
    user = CurrentUser(
        user_id=sale.cashier_user_id,
        tenant_id=sale.tenant_id,
        is_developer=False,
        is_administrator=False,
        permissions={"sales.view.own"},
        permission_scopes={"sales.view.own": frozenset({sale.branch_id})},
    )
    # Direct invocation tests dispatch; real Depends and SQL RLS remain CI regressions elsewhere.
    details = await get_sale(sale.id, user, service)
    receipt = await get_receipt(sale.id, user, service)
    assert details.items == details.physical_items == details.prepared_cart_items == []
    assert details.mixed_items == receipt.items
    assert len(details.mixed_items) == 2
    assert details.mixed_items[0].name == "Сохранённое обычное лекарство"
    assert service.get_receipt_item_names(sale) == {}
    assert service.get_physical_receipt_items(sale) == []
    assert details.payments[0].amount == receipt.total
    repository.list_prepared_cart_items.assert_not_awaited()


@pytest.mark.parametrize(
    "version,kind", [(5, "mixed_receipt"), (4, "physical_receipt"), (4, "mixed_checkout")]
)
async def test_unsupported_snapshots_never_fall_back_to_live_catalog_or_legacy_receipt(
    monkeypatch: pytest.MonkeyPatch, version: int, kind: str
) -> None:
    sale = synthetic_sale()
    payload = deepcopy(sale.receipt_snapshot)
    assert payload is not None
    payload.update(schema_version=version, document_kind=kind)
    sale.receipt_snapshot = payload
    service, repository = controlled_service(sale, monkeypatch)
    with pytest.raises(AurumError):
        await service.build_receipt(
            sale.id, viewer_id=sale.cashier_user_id, allowed_branch_ids={sale.branch_id}
        )
    repository.session.get.assert_not_awaited()
    repository.list_items.assert_not_awaited()
    repository.list_payments.assert_not_awaited()


@pytest.mark.parametrize("requested_items", [False, True])
async def test_mixed_history_does_not_enable_legacy_or_electronic_refund_calculation(
    monkeypatch: pytest.MonkeyPatch, requested_items: bool
) -> None:
    sale = synthetic_sale()
    service, repository = controlled_service(sale, monkeypatch)
    history = AsyncMock(side_effect=AssertionError("Mixed refunds must stop before history reads"))
    validate = AsyncMock(
        side_effect=AssertionError("Mixed refunds must stop before item allocation")
    )
    monkeypatch.setattr(service, "_assert_consumed_refund_history", history)
    monkeypatch.setattr(service, "_validate_refund_items", validate)
    before = deepcopy(sale.receipt_snapshot)
    with pytest.raises(BusinessRuleError, match="Mixed sale refunds are not supported yet"):
        await service._calculate_refund_financials(
            parent=sale,
            per_item={UUID(int=40): Decimal("0.001")} if requested_items else {},
        )
    history.assert_not_awaited()
    validate.assert_not_awaited()
    assert repository.mock_calls == []
    assert sale.receipt_snapshot == before
