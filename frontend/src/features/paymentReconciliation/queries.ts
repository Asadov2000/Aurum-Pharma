import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { confirmPaymentAttempt, voidPaymentAttempt } from "@/features/pos/api";
import {
  type PaymentAttemptConfirmPayload,
  type PaymentAttemptVoidPayload,
} from "@/features/pos/types";

import { listPaymentReconciliation, listRefundReconciliation } from "./api";
import { type PaymentReconciliationParams, type RefundReconciliationParams } from "./types";

export const paymentReconciliationKeys = {
  root: ["payment-reconciliation"] as const,
  list: (params: PaymentReconciliationParams) => ["payment-reconciliation", params] as const,
};

export const refundReconciliationKeys = {
  root: ["refund-reconciliation"] as const,
  list: (params: RefundReconciliationParams) => ["refund-reconciliation", params] as const,
};

export function usePaymentReconciliationQuery(params: PaymentReconciliationParams, enabled = true) {
  return useQuery({
    queryKey: paymentReconciliationKeys.list(params),
    queryFn: () => listPaymentReconciliation(params),
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
    enabled,
  });
}

export function useRefundReconciliationQuery(params: RefundReconciliationParams, enabled = true) {
  return useQuery({
    queryKey: refundReconciliationKeys.list(params),
    queryFn: () => listRefundReconciliation(params),
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
    enabled,
  });
}

export function useConfirmReconciliation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: PaymentAttemptConfirmPayload }) =>
      confirmPaymentAttempt(id, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: paymentReconciliationKeys.root }),
  });
}

export function useVoidReconciliation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: PaymentAttemptVoidPayload }) =>
      voidPaymentAttempt(id, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: paymentReconciliationKeys.root }),
  });
}
