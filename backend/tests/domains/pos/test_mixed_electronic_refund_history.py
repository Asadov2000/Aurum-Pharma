"""Pure mixed refund history checks never infer payment facts from returned stock."""

from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.errors import AurumError, BusinessRuleError
from app.domains.pos.mixed_documents import MixedCheckoutResultV4, build_mixed_receipt
from app.domains.pos.mixed_electronic_refund_history import (
    original_electronic_checkout,
    require_reconciled_history,
    returned_goods,
    returned_payments,
)
from app.domains.pos.mixed_electronic_refund_history_schemas import (
    MixedElectronicRefundHistory,
    MixedRefundComponentFact,
    MixedReturnLineFact,
)
from app.domains.pos.mixed_return_allocation import LegacyReturnSelection
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from app.domains.pos.unit_electronic_refund_preview_schemas import RefundComponentFact
from app.domains.pos.unit_payment_refund_allocation import plan_mixed_payment_refund
from tests.domains.pos.test_mixed_return_allocation import checkout


def history() -> MixedElectronicRefundHistory:
    value = checkout()
    operation = uuid4()
    payments = [
        {"id": uuid4(), "method": "cash", "amount": "1.02"},
        {"id": uuid4(), "method": "card", "amount": "9.00"},
        {"id": uuid4(), "method": "qr", "amount": "10.00"},
    ]
    original = MixedCheckoutResultV4.model_validate(
        {
            **value.model_dump(),
            "operation_id": operation,
            "stock_command_id": operation,
            "event_id": operation,
            "is_test": False,
            "payments": payments,
        }
    )
    receipt = build_mixed_receipt(original)
    scope = {"tenant_id": original.tenant_id, "sale_id": original.sale_id}
    return MixedElectronicRefundHistory.model_validate(
        {
            "sale": {
                **scope,
                "id": original.sale_id,
                "branch_id": original.branch_id,
                "register_id": original.register_id,
                "shift_id": original.shift_id,
                "cashier_user_id": original.cashier_id,
                "parent_sale_id": None,
                "sale_type": "sale",
                "status": "completed",
                "currency": "TJS",
                "is_test": False,
                "total_amount": original.total_amount,
                "created_at": original.created_at,
                "completed_at": original.completed_at,
                "receipt_number": original.receipt_number,
                "receipt_seq": original.receipt_sequence,
                "receipt_snapshot": receipt,
            },
            "checkout": {
                **scope,
                "id": operation,
                "branch_id": original.branch_id,
                "register_id": original.register_id,
                "shift_id": original.shift_id,
                "cashier_user_id": original.cashier_id,
                "cash_intent_id": None,
                "payment_plan_intent_id": operation,
                "stock_command_id": operation,
                "payment_id": operation,
                "payment_journal_version": 3,
                "request_hash": "a" * 64,
                "result_payload": original,
                "receipt_payload": receipt,
            },
            "payments": [
                {
                    **scope,
                    "id": part.id,
                    "payment_method": part.method,
                    "amount": part.amount,
                    "currency": "TJS",
                    "operation_id": part.id if part.method == "cash" else uuid4(),
                    "operation_hash": "a" * 64,
                    "payment_attempt_id": None if part.method == "cash" else part.id,
                    "metadata": None,
                }
                for part in original.payments
            ],
            "components": [
                {
                    **scope,
                    "id": part.id,
                    "physical_checkout_id": None,
                    "mixed_checkout_id": operation,
                    "method": part.method,
                    "amount": part.amount,
                    "currency": "TJS",
                    "payment_attempt_id": None if part.method == "cash" else part.id,
                    "created_by": original.cashier_id,
                }
                for part in original.payments
            ],
            "return_lines": [],
            "refund_sales": [],
            "payouts": [],
            "refund_components": [],
        }
    )


def legacy_return(h: MixedElectronicRefundHistory) -> MixedReturnLineFact:
    source = h.checkout.result_payload
    line = source.lines[0]
    return MixedReturnLineFact.model_validate(
        {
            "tenant_id": source.tenant_id,
            "command_id": uuid4(),
            "line_id": line.id,
            "branch_id": source.branch_id,
            "parent_checkout_id": source.operation_id,
            "stock_command_id": source.stock_command_id,
            "inventory_kind": "legacy_pack",
            "catalog_id": line.catalog_id,
            "batch_id": line.batch_id,
            "legacy_quantity": "1.000",
            "legacy_offset": "0.000",
            "offset": None,
            "base_units": None,
            "sale_format": None,
            "quantity": None,
            "net_amount": "3.33",
            "discount_amount": "0.66",
            "gross_amount": "3.99",
        }
    )


def test_original_mixed_electronic_source_and_empty_history_are_exact() -> None:
    h = history()
    source = original_electronic_checkout(h, h.sale.tenant_id, h.sale.id)
    assert source == h.checkout.result_payload
    assert returned_goods(h, source) == ({}, {})
    assert returned_payments(h, source) == {}
    require_reconciled_history(h, source, {})
    with pytest.raises(AurumError):
        original_electronic_checkout(h, uuid4(), h.sale.id)


@pytest.mark.parametrize("mutation", ["cash-source", "physical-component", "amount", "posting-id"])
def test_bad_source_or_original_payment_facts_fail_closed(mutation: str) -> None:
    h = history()
    if mutation == "cash-source":
        h = h.model_copy(
            update={"checkout": h.checkout.model_copy(update={"payment_journal_version": 2})}
        )
    elif mutation == "physical-component":
        part = h.components[0].model_copy(update={"physical_checkout_id": h.checkout.id})
        h = h.model_copy(update={"components": (part, *h.components[1:])})
    else:
        change = (
            {"amount": Decimal("9.01")} if mutation == "amount" else {"operation_id": h.checkout.id}
        )
        part = h.payments[1].model_copy(update=change)
        h = h.model_copy(update={"payments": (h.payments[0], part, h.payments[2])})
    with pytest.raises((AurumError, BusinessRuleError)):
        original_electronic_checkout(h, h.sale.tenant_id, h.sale.id)


def test_valid_legacy_floor_cursor_still_requires_actual_payment_history() -> None:
    h = history()
    row = legacy_return(h)
    h = h.model_copy(update={"return_lines": (row,)})
    source = original_electronic_checkout(h, h.sale.tenant_id, h.sale.id)
    assert returned_goods(h, source) == ({row.line_id: Decimal("1.000")}, {})
    with pytest.raises(AurumError, match="reconciliation"):
        require_reconciled_history(h, source, returned_payments(h, source))


@pytest.mark.parametrize("mutation", ["gap", "batch", "kind", "net", "parent"])
def test_corrupt_legacy_history_never_becomes_a_trusted_cursor(mutation: str) -> None:
    h = history()
    changes: dict[str, object] = {
        "gap": {"legacy_offset": Decimal("1.000")},
        "batch": {"batch_id": uuid4()},
        "kind": {"inventory_kind": "physical_unit"},
        "net": {"net_amount": Decimal("3.34")},
        "parent": {"parent_checkout_id": uuid4()},
    }[mutation]
    row = legacy_return(h).model_copy(update=changes)
    h = h.model_copy(update={"return_lines": (row,)})
    with pytest.raises(AurumError):
        returned_goods(h, h.checkout.result_payload)


@pytest.mark.parametrize("mutation", ["valid", "gap", "units", "amount"])
def test_physical_history_uses_its_saved_whole_units(mutation: str) -> None:
    h = history()
    source = h.checkout.result_payload
    line = source.lines[1]
    row = MixedReturnLineFact.model_validate(
        {
            "tenant_id": source.tenant_id,
            "command_id": uuid4(),
            "line_id": line.id,
            "branch_id": source.branch_id,
            "parent_checkout_id": source.operation_id,
            "stock_command_id": source.stock_command_id,
            "inventory_kind": "physical_unit",
            "catalog_id": line.catalog_id,
            "batch_id": None,
            "legacy_quantity": None,
            "legacy_offset": None,
            "offset": 0,
            "base_units": 1,
            "sale_format": "unit",
            "quantity": 1,
            "net_amount": "0.33",
            "discount_amount": "0.03",
            "gross_amount": "0.36",
        }
    )
    changes: dict[str, object] = {
        "valid": {},
        "gap": {"offset": 1},
        "units": {"base_units": 2},
        "amount": {"net_amount": Decimal("0.34")},
    }[mutation]
    h = h.model_copy(update={"return_lines": (row.model_copy(update=changes),)})
    if mutation == "valid":
        assert returned_goods(h, source) == ({}, {line.id: 1})
    else:
        with pytest.raises(AurumError):
            returned_goods(h, source)


@pytest.mark.parametrize("method", ["card", "qr"])
def test_electronic_payout_cannot_use_cash_refund_marker(method: str) -> None:
    h = history()
    original = h.checkout.result_payload
    source = next(part for part in original.payments if part.method == method)
    refund_id, sale_id, payment_id = uuid4(), uuid4(), uuid4()
    data = h.model_dump()
    data.update(
        refund_sales=[
            {
                "id": sale_id,
                "tenant_id": original.tenant_id,
                "branch_id": original.branch_id,
                "parent_sale_id": original.sale_id,
                "status": "completed",
                "sale_type": "return",
                "currency": "TJS",
                "is_test": False,
                "total_amount": "0.01",
                "cashier_user_id": original.cashier_id,
            }
        ],
        payouts=[
            {
                "id": payment_id,
                "tenant_id": original.tenant_id,
                "sale_id": sale_id,
                "payment_method": method,
                "amount": "0.01",
                "currency": "TJS",
                "operation_id": refund_id,
                "operation_hash": "b" * 64,
                "payment_attempt_id": None,
                "metadata": None,
            }
        ],
        refund_components=[
            {
                "id": payment_id,
                "tenant_id": original.tenant_id,
                "parent_sale_id": original.sale_id,
                "sale_id": sale_id,
                "original_payment_id": source.id,
                "physical_refund_id": None,
                "mixed_refund_id": refund_id,
                "electronic_refund_id": None,
                "method": method,
                "amount": "0.01",
                "currency": "TJS",
                "created_by": original.cashier_id,
            }
        ],
    )
    with pytest.raises(AurumError, match="payment facts"):
        returned_payments(MixedElectronicRefundHistory.model_validate(data), original)


def test_reconciled_partial_mixed_history_leaves_each_original_payment_balance() -> None:
    h = history()
    original = h.checkout.result_payload
    refund_id, sale_id, payment_id = uuid4(), uuid4(), uuid4()
    row = legacy_return(h).model_copy(
        update={
            "legacy_quantity": Decimal("0.300"),
            "net_amount": Decimal("1.00"),
            "discount_amount": Decimal("0.19"),
            "gross_amount": Decimal("1.19"),
        }
    )
    cash = next(part for part in original.payments if part.method == "cash")
    data = h.model_dump()
    data.update(
        return_lines=[row],
        refund_sales=[
            {
                "id": sale_id,
                "tenant_id": original.tenant_id,
                "branch_id": original.branch_id,
                "parent_sale_id": original.sale_id,
                "status": "completed",
                "sale_type": "return",
                "currency": "TJS",
                "is_test": False,
                "total_amount": "1.00",
                "cashier_user_id": original.cashier_id,
            }
        ],
        payouts=[
            {
                "id": payment_id,
                "tenant_id": original.tenant_id,
                "sale_id": sale_id,
                "payment_method": "cash",
                "amount": "1.00",
                "currency": "TJS",
                "operation_id": refund_id,
                "operation_hash": "b" * 64,
                "payment_attempt_id": None,
                "metadata": None,
            }
        ],
        refund_components=[
            {
                "id": payment_id,
                "tenant_id": original.tenant_id,
                "parent_sale_id": original.sale_id,
                "sale_id": sale_id,
                "original_payment_id": cash.id,
                "physical_refund_id": None,
                "mixed_refund_id": refund_id,
                "electronic_refund_id": None,
                "method": "cash",
                "amount": "1.00",
                "currency": "TJS",
                "created_by": original.cashier_id,
            }
        ],
    )
    h = MixedElectronicRefundHistory.model_validate(data)
    legacy, physical = returned_goods(h, original)
    payments = returned_payments(h, original)
    require_reconciled_history(h, original, payments)
    plan = plan_mixed_payment_refund(
        original,
        (
            LegacyReturnSelection(original.lines[0].id, Decimal("2.700")),
            PhysicalReturnSelection(original.lines[1].id, "pack", 1),
        ),
        returned_legacy_quantities=legacy,
        returned_base_units=physical,
        returned_payment_amounts=payments,
    )
    assert plan.total_amount == Decimal("19.02")
    assert {part.method: part.amount for part in plan.payments} == {
        "cash": Decimal("0.02"),
        "card": Decimal("9.00"),
        "qr": Decimal("10.00"),
    }


def mixed_posting_history(method: str) -> MixedElectronicRefundHistory:
    h = history()
    original = h.checkout.result_payload
    source = next(part for part in original.payments if part.method == method)
    operation, sale_id, payment_id = uuid4(), uuid4(), uuid4()
    data = h.model_dump()
    data.update(
        refund_sales=[
            {
                "id": sale_id,
                "tenant_id": original.tenant_id,
                "branch_id": original.branch_id,
                "parent_sale_id": original.sale_id,
                "status": "completed",
                "sale_type": "return",
                "currency": "TJS",
                "is_test": False,
                "total_amount": "0.01",
                "cashier_user_id": original.cashier_id,
            }
        ],
        payouts=[
            {
                "id": payment_id,
                "tenant_id": original.tenant_id,
                "sale_id": sale_id,
                "payment_method": method,
                "amount": "0.01",
                "currency": "TJS",
                "operation_id": None,
                "operation_hash": None,
                "payment_attempt_id": None,
                "metadata": (
                    None
                    if method == "cash"
                    else {
                        "terminal_id": "synthetic-terminal",
                        "document_number": "synthetic-document",
                    }
                ),
            }
        ],
        refund_components=[
            {
                "id": payment_id,
                "tenant_id": original.tenant_id,
                "parent_sale_id": original.sale_id,
                "sale_id": sale_id,
                "original_payment_id": source.id,
                "physical_refund_id": None,
                "mixed_refund_id": None,
                "electronic_refund_id": None,
                "mixed_electronic_refund_id": operation,
                "method": method,
                "amount": "0.01",
                "currency": "TJS",
                "created_by": original.cashier_id,
            }
        ],
    )
    return MixedElectronicRefundHistory.model_validate(data)


@pytest.mark.parametrize("method", ["cash", "card", "qr"])
@pytest.mark.parametrize("mutation", ["valid", "ambiguous", "missing", "physical", "metadata"])
def test_mixed_posting_payment_parent_and_evidence_remain_strict(
    method: str,
    mutation: str,
) -> None:
    h = mixed_posting_history(method)
    component = h.refund_components[0]
    if mutation in {"ambiguous", "missing", "physical"}:
        changes = {
            "ambiguous": {"mixed_refund_id": uuid4()},
            "missing": {"mixed_electronic_refund_id": None},
            "physical": {"physical_refund_id": uuid4()},
        }[mutation]
        h = h.model_copy(update={"refund_components": (component.model_copy(update=changes),)})
    elif mutation == "metadata":
        payout = h.payouts[0].model_copy(
            update={"metadata": {"unexpected": "data"} if method == "cash" else None}
        )
        h = h.model_copy(update={"payouts": (payout,)})
    if mutation != "valid":
        with pytest.raises(AurumError):
            returned_payments(h, h.checkout.result_payload)
    else:
        assert returned_payments(h, h.checkout.result_payload) == {
            component.original_payment_id: Decimal("0.01")
        }


def test_absent_new_parent_preserves_existing_physical_and_mixed_history_serialization() -> None:
    data = mixed_posting_history("cash").refund_components[0].model_dump()
    data.pop("mixed_electronic_refund_id")
    data["mixed_refund_id"] = uuid4()
    original = RefundComponentFact.model_validate(data)
    extended = MixedRefundComponentFact.model_validate(data)
    assert extended.mixed_electronic_refund_id is None
    assert extended.model_dump() == original.model_dump()
    assert extended.model_dump(mode="json") == original.model_dump(mode="json")
    assert extended.model_dump_json() == original.model_dump_json()
