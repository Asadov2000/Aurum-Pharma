import { api } from "@/lib/api";

import { type PaymentReconciliationList, type PaymentReconciliationParams } from "./types";

export async function listPaymentReconciliation(
  params: PaymentReconciliationParams,
): Promise<PaymentReconciliationList> {
  const { data } = await api.get<PaymentReconciliationList>("/pos/payment-reconciliation", {
    params,
  });
  return data;
}
