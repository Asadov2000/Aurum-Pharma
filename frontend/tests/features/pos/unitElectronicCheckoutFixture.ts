import {
  type ElectronicCheckoutContext,
  type ElectronicAttempt,
} from "@/features/pos/unitElectronicCheckout";
import { bound, id, quote, saved, scope } from "./unitPaymentPreparationFixture";
export const context = (): ElectronicCheckoutContext => ({
  intent: saved,
  binding: bound,
  scope,
  branchId: quote.branch_id,
  registerId: quote.register_id,
  isActive: () => true,
});
export const attempt = (status: ElectronicAttempt["status"] = "pending"): ElectronicAttempt => ({
  id: id(21),
  operation_id: id(21),
  tenant_id: scope.tenantId,
  cashier_user_id: scope.userId,
  sale_id: quote.sale_id,
  payment_method: "card",
  amount: "1.99",
  currency: "TJS",
  status,
  terminal_id: status === "confirmed" || status === "consumed" ? "T1" : null,
  external_reference: status === "confirmed" || status === "consumed" ? "R1" : null,
  resolved_by_user_id: null,
  reconciliation_started_at:
    status === "requires_reconciliation" || status === "confirmed" || status === "consumed"
      ? "2026-10-20T12:01:30Z"
      : null,
  evidence_required: true,
  void_reason: null,
  void_note: null,
  created_at: "2026-10-20T12:01:00Z",
  confirmed_at: status === "confirmed" || status === "consumed" ? "2026-10-20T12:02:00Z" : null,
  consumed_at: status === "consumed" ? "2026-10-20T12:03:00Z" : null,
  voided_at: null,
});
export const result = () => ({
  schema_version: 3,
  document_kind: "physical_checkout",
  tenant_id: scope.tenantId,
  cashier_id: scope.userId,
  branch_id: quote.branch_id,
  register_id: quote.register_id,
  shift_id: quote.shift_id,
  sale_id: quote.sale_id,
  operation_id: saved.operation_id,
  event_id: saved.operation_id,
  stock_command_id: saved.operation_id,
  created_at: "2026-10-20T12:01:00Z",
  completed_at: "2026-10-20T12:03:00Z",
  receipt_number: "001",
  receipt_sequence: 1,
  is_test: false,
  lines: quote.lines.map((l) => ({ ...l, name: "Historical name" })),
  payments: [{ id: id(21), method: "card", amount: "1.99", currency: "TJS" }],
  total_amount: "1.99",
  currency: "TJS",
});
