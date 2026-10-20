import { type SaleDetails } from "@/features/pos/types";

/** Details have no journal marker. Only the electronic quote proves server support. */
export function isElectronicRefundCandidate(sale: SaleDetails): boolean {
  return (
    sale.sale_type === "sale" &&
    sale.status === "completed" &&
    !sale.is_test &&
    (sale.physical_items?.length ?? 0) > 0 &&
    !sale.mixed_items?.length &&
    !sale.prepared_cart_items?.length &&
    sale.payments.some(
      (p) =>
        (p.payment_method === "card" || p.payment_method === "qr") &&
        /^(0|[1-9]\d{0,11})\.\d{2}$/.test(p.amount) &&
        BigInt(p.amount.replace(".", "")) > 0n,
    )
  );
}
