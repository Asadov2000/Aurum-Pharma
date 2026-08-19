import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createPaymentSubmission,
  getFinancialAccount,
  listPaymentSubmissions,
  withdrawPaymentSubmission,
} from "./api";
import {
  type TenantPaymentSubmissionCreate,
  type TenantPaymentSubmissionWithdraw,
} from "./types";

export const billingKeys = {
  financialAccount: ["billing", "financial-account"] as const,
  paymentSubmissions: (page: number, pageSize: number) =>
    ["billing", "payment-submissions", page, pageSize] as const,
};

export function useFinancialAccountQuery() {
  return useQuery({
    queryKey: billingKeys.financialAccount,
    queryFn: ({ signal }) => getFinancialAccount(signal),
    staleTime: 15_000,
  });
}

export function usePaymentSubmissionsQuery(page: number, pageSize: number, enabled = true) {
  return useQuery({
    queryKey: billingKeys.paymentSubmissions(page, pageSize),
    queryFn: ({ signal }) => listPaymentSubmissions(page, pageSize, signal),
    enabled,
    staleTime: 15_000,
  });
}

export function useCreatePaymentSubmission() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: TenantPaymentSubmissionCreate) => createPaymentSubmission(payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["billing", "payment-submissions"] });
    },
  });
}

export function useWithdrawPaymentSubmission() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      submissionId,
      payload,
    }: {
      submissionId: string;
      payload: TenantPaymentSubmissionWithdraw;
    }) => withdrawPaymentSubmission(submissionId, payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["billing", "payment-submissions"] });
    },
  });
}
