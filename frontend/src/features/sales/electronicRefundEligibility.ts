import { type SaleDetails } from "@/features/pos/types";

/** The mixed electronic quote remains authoritative for immutable payment history. */
export function isMixedElectronicRefundCandidate(sale: SaleDetails): boolean {
  return (
    sale.sale_type === "sale" &&
    sale.status === "completed" &&
    !sale.is_test &&
    (sale.mixed_items?.length ?? 0) > 0 &&
    sale.payments.some(
      (payment) =>
        (payment.payment_method === "card" || payment.payment_method === "qr") &&
        /^(0|[1-9]\d{0,11})\.\d{2}$/.test(payment.amount) &&
        BigInt(payment.amount.replace(".", "")) > 0n,
    )
  );
}

/** Details have no journal marker. Only the electronic quote proves server support. */
export function isElectronicRefundCandidate(sale: SaleDetails): boolean {
  return (
    sale.sale_type === "sale" &&
    sale.status === "completed" &&
    !sale.is_test &&
    (sale.physical_items?.length ?? 0) > 0 &&
    !sale.mixed_items?.length &&
    // Completed physical checkouts retain their original cart rows in this field.
    // Receipt display + the authoritative electronic quote distinguish real v3 history.
    sale.payments.some(
      (p) =>
        (p.payment_method === "card" || p.payment_method === "qr") &&
        /^(0|[1-9]\d{0,11})\.\d{2}$/.test(p.amount) &&
        BigInt(p.amount.replace(".", "")) > 0n,
    )
  );
}
