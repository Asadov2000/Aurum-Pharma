import { type SaleDetails } from "./types";
import { type ElectronicCheckoutResult } from "./unitElectronicCheckout";

type CanonicalSale = Pick<
  SaleDetails,
  | "status"
  | "is_test"
  | "id"
  | "tenant_id"
  | "cashier_user_id"
  | "branch_id"
  | "register_id"
  | "shift_id"
  | "receipt_number"
  | "total_amount"
  | "currency"
  | "payments"
>;
const uuid4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

/** Electronic payment IDs bind the attempt; their posting operation is a separate UUID. */
export function matchesElectronicCompletedSale(
  sale: CanonicalSale,
  result: ElectronicCheckoutResult,
): boolean {
  return (
    sale.status === "completed" &&
    sale.is_test === false &&
    sale.id === result.sale_id &&
    sale.tenant_id === result.tenant_id &&
    sale.cashier_user_id === result.cashier_id &&
    sale.branch_id === result.branch_id &&
    sale.register_id === result.register_id &&
    sale.shift_id === result.shift_id &&
    sale.receipt_number === result.receipt_number &&
    sale.total_amount === result.total_amount &&
    sale.currency === result.currency &&
    sale.payments.length === result.payments.length &&
    new Set(sale.payments.map((p) => p.id)).size === sale.payments.length &&
    new Set(sale.payments.map((p) => p.operation_id)).size === sale.payments.length &&
    result.payments.every((part) =>
      sale.payments.some(
        (payment) =>
          payment.id === part.id &&
          payment.sale_id === result.sale_id &&
          payment.payment_method === part.method &&
          payment.amount === part.amount &&
          payment.currency === part.currency &&
          (part.method === "cash"
            ? payment.operation_id === part.id && payment.payment_attempt_id == null
            : payment.operation_id !== null &&
              uuid4.test(payment.operation_id) &&
              payment.operation_id !== part.id &&
              payment.operation_id !== result.operation_id &&
              (payment.payment_attempt_id === undefined || payment.payment_attempt_id === part.id)),
      ),
    )
  );
}
