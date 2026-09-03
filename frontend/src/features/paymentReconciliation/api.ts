import { api } from "@/lib/api";

import {
  type PaymentReconciliationList,
  type PaymentReconciliationParams,
  type RefundReconciliationList,
  type RefundReconciliationParams,
} from "./types";

export async function listPaymentReconciliation(
  params: PaymentReconciliationParams,
): Promise<PaymentReconciliationList> {
  const { data } = await api.get<PaymentReconciliationList>("/pos/payment-reconciliation", {
    params,
  });
  return data;
}

export async function listRefundReconciliation(
  params: RefundReconciliationParams,
): Promise<RefundReconciliationList> {
  const { data } = await api.get<RefundReconciliationList>("/pos/refund-reconciliation", {
    params,
  });
  return data;
}
