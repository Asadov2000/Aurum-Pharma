import { describe, expect, it } from "vitest";
import { matchesElectronicCompletedSale } from "@/features/pos/electronicCheckoutReceipt";
import { type ElectronicCheckoutResult } from "@/features/pos/unitElectronicCheckout";
import { result } from "./unitElectronicCheckoutFixture";
import { id } from "./unitPaymentPreparationFixture";

function canonical(saved: ElectronicCheckoutResult) {
  return {
    status: "completed" as const,
    is_test: false,
    id: saved.sale_id,
    tenant_id: saved.tenant_id,
    cashier_user_id: saved.cashier_id,
    branch_id: saved.branch_id,
    register_id: saved.register_id,
    shift_id: saved.shift_id,
    receipt_number: saved.receipt_number,
    total_amount: saved.total_amount,
    currency: saved.currency,
    payments: saved.payments.map((p, i) => ({
      id: p.id,
      sale_id: saved.sale_id,
      operation_id: p.method === "cash" ? p.id : id(70 + i),
      payment_method: p.method,
      amount: p.amount,
      currency: p.currency,
    })),
  };
}
const saved = () => result() as ElectronicCheckoutResult;
describe("canonical electronic posting receipt", () => {
  it("accepts the real backend's separate posting UUID for card and QR plus cash", () => {
    for (const method of ["card", "qr"] as const) {
      const doc = saved();
      doc.payments = [
        { ...doc.payments[0]!, method, amount: "1.00" },
        { id: doc.operation_id, method: "cash", amount: "0.99", currency: "TJS" },
      ];
      expect(matchesElectronicCompletedSale(canonical(doc), doc)).toBe(true);
    }
  });
  it("rejects absent, attempt-equal or checkout-equal electronic operation IDs", () => {
    const doc = saved(),
      sale = canonical(doc);
    for (const operation_id of [
      doc.payments[0]!.id,
      doc.operation_id,
      id(70).replace("4000", "1000"),
      "invalid",
    ]) {
      expect(
        matchesElectronicCompletedSale(
          { ...sale, payments: [{ ...sale.payments[0]!, operation_id }] },
          doc,
        ),
      ).toBe(false);
    }
    expect(
      matchesElectronicCompletedSale(
        { ...sale, payments: [{ ...sale.payments[0]!, operation_id: null }] },
        doc,
      ),
    ).toBe(false);
    expect(
      matchesElectronicCompletedSale(
        { ...sale, payments: [{ ...sale.payments[0]!, payment_attempt_id: id(99) }] },
        doc,
      ),
    ).toBe(false);
    expect(
      matchesElectronicCompletedSale(
        { ...sale, payments: [{ ...sale.payments[0]!, payment_attempt_id: doc.payments[0]!.id }] },
        doc,
      ),
    ).toBe(true);
  });
  it("retains exact owner, receipt, allocation and payment provenance checks", () => {
    const doc = saved(),
      sale = canonical(doc);
    for (const patch of [
      { id: id(99) },
      { tenant_id: id(99) },
      { cashier_user_id: id(99) },
      { branch_id: id(99) },
      { register_id: id(99) },
      { shift_id: id(99) },
      { receipt_number: "alien" },
      { total_amount: "2.00" },
      { currency: "USD" },
      { status: "draft" as const },
      { is_test: true },
    ])
      expect(matchesElectronicCompletedSale({ ...sale, ...patch }, doc)).toBe(false);
    for (const patch of [
      { id: id(99) },
      { sale_id: id(99) },
      { amount: "2.00" },
      { currency: "USD" },
      { payment_method: "qr" as const },
    ])
      expect(
        matchesElectronicCompletedSale(
          { ...sale, payments: [{ ...sale.payments[0]!, ...patch }] },
          doc,
        ),
      ).toBe(false);
    expect(
      matchesElectronicCompletedSale(
        { ...sale, payments: [...sale.payments, ...sale.payments] },
        doc,
      ),
    ).toBe(false);
  });
});

it("preserves exact cash provenance and the complete three-method allocation", () => {
  const doc = saved();
  doc.payments = [
    { id: id(21), method: "card", amount: "0.50", currency: "TJS" },
    { id: id(22), method: "qr", amount: "0.50", currency: "TJS" },
    { id: doc.operation_id, method: "cash", amount: "0.99", currency: "TJS" },
  ];
  const sale = canonical(doc);
  expect(matchesElectronicCompletedSale(sale, doc)).toBe(true);
  for (const cash of [
    { ...sale.payments[2]!, operation_id: id(90) },
    { ...sale.payments[2]!, payment_attempt_id: id(21) },
    { ...sale.payments[2]!, amount: "1.00" },
  ])
    expect(
      matchesElectronicCompletedSale(
        { ...sale, payments: [...sale.payments.slice(0, 2), cash] },
        doc,
      ),
    ).toBe(false);
  expect(
    matchesElectronicCompletedSale({ ...sale, payments: sale.payments.slice(0, 2) }, doc),
  ).toBe(false);
  expect(
    matchesElectronicCompletedSale(
      {
        ...sale,
        payments: sale.payments.map((p) =>
          p.payment_method === "qr" ? { ...p, operation_id: sale.payments[0]!.operation_id } : p,
        ),
      },
      doc,
    ),
  ).toBe(false);
});
