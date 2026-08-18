import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  activatePlatformPricingPrice,
  approvePlatformBankPayment,
  approvePlatformPaymentAdjustment,
  cancelPlatformPricingPrice,
  createPlatformBankPaymentReview,
  createPlatformPaymentAdjustment,
  createPlatformPricingPlan,
  createPlatformPricingPrice,
  getPlatformPaymentApprovalDetail,
  getPlatformPaymentSubmission,
  getPlatformFinancialAccount,
  getPlatformBillingOverview,
  listPlatformBillingTenants,
  listPlatformInvoices,
  listPlatformPaymentApprovalQueue,
  listPlatformPaymentAdjustmentQueue,
  listPlatformPaymentSubmissions,
  listPlatformPricingPlans,
  schedulePlatformPricingPrice,
  rejectPlatformBankPaymentReview,
  rejectPlatformPaymentSubmission,
  rejectPlatformPaymentAdjustment,
  reviewPlatformPaymentSubmission,
} from "./api";
import {
  type PlatformBankPaymentApprove,
  type PlatformBankPaymentReviewReject,
  type PlatformBankPaymentReviewCreate,
  type PlatformBillingTenantFilters,
  type PlatformInvoiceFilters,
  type PlatformPaymentAdjustmentApprove,
  type PlatformPaymentAdjustmentCreate,
  type PlatformPaymentAdjustmentReject,
  type PlatformPaymentSubmissionReject,
  type PlatformPaymentSubmissionReview,
  type PricingActivate,
  type PricingCancel,
  type PricingPlanCreate,
  type PricingPriceDraftCreate,
  type PricingSchedule,
} from "./types";

export const platformBillingKeys = {
  all: ["platform-billing"] as const,
  overview: () => [...platformBillingKeys.all, "overview"] as const,
  invoices: (filters: PlatformInvoiceFilters) =>
    [...platformBillingKeys.all, "invoices", filters] as const,
  pricingPlans: (page: number, pageSize: number) =>
    [...platformBillingKeys.all, "pricing-plans", page, pageSize] as const,
  financialAccount: (tenantId: string) =>
    [...platformBillingKeys.all, "v2", "financial-account", tenantId] as const,
  tenants: (filters: PlatformBillingTenantFilters) =>
    [...platformBillingKeys.all, "v2", "tenants", filters] as const,
  approvalQueue: (tenantId: string, page: number, pageSize: number) =>
    [...platformBillingKeys.all, "v2", "approval-queue", tenantId, page, pageSize] as const,
  approvalDetail: (tenantId: string, reviewId: string) =>
    [...platformBillingKeys.all, "v2", "approval-detail", tenantId, reviewId] as const,
  adjustmentQueue: (tenantId: string, page: number, pageSize: number) =>
    [...platformBillingKeys.all, "v2", "adjustment-queue", tenantId, page, pageSize] as const,
  submissionQueue: (tenantId: string, page: number, pageSize: number) =>
    [...platformBillingKeys.all, "v2", "submission-queue", tenantId, page, pageSize] as const,
  submissionDetail: (tenantId: string, submissionId: string) =>
    [...platformBillingKeys.all, "v2", "submission-detail", tenantId, submissionId] as const,
};

export function usePlatformBillingOverview(enabled: boolean) {
  return useQuery({
    queryKey: platformBillingKeys.overview(),
    queryFn: ({ signal }) => getPlatformBillingOverview(signal),
    enabled,
    staleTime: 30_000,
  });
}

export function usePlatformInvoices(filters: PlatformInvoiceFilters, enabled: boolean) {
  return useQuery({
    queryKey: platformBillingKeys.invoices(filters),
    queryFn: ({ signal }) => listPlatformInvoices(filters, signal),
    enabled,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}

export function usePlatformPricingPlans(page: number, pageSize: number, enabled: boolean) {
  return useQuery({
    queryKey: platformBillingKeys.pricingPlans(page, pageSize),
    queryFn: ({ signal }) => listPlatformPricingPlans(page, pageSize, signal),
    enabled,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}

export function usePlatformFinancialAccount(tenantId: string, enabled: boolean) {
  return useQuery({
    queryKey: platformBillingKeys.financialAccount(tenantId),
    queryFn: ({ signal }) => getPlatformFinancialAccount(tenantId, signal),
    enabled: enabled && tenantId.length > 0,
    staleTime: 15_000,
  });
}

export function usePlatformBillingTenants(filters: PlatformBillingTenantFilters, enabled: boolean) {
  return useQuery({
    queryKey: platformBillingKeys.tenants(filters),
    queryFn: ({ signal }) => listPlatformBillingTenants(filters, signal),
    enabled,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}

export function usePlatformPaymentApprovalQueue(
  tenantId: string,
  page: number,
  pageSize: number,
  enabled: boolean,
) {
  return useQuery({
    queryKey: platformBillingKeys.approvalQueue(tenantId, page, pageSize),
    queryFn: ({ signal }) => listPlatformPaymentApprovalQueue(tenantId, page, pageSize, signal),
    enabled: enabled && tenantId.length > 0,
    placeholderData: keepPreviousData,
    staleTime: 5_000,
  });
}

export function usePlatformPaymentApprovalDetail(
  tenantId: string,
  reviewId: string,
  enabled: boolean,
) {
  return useQuery({
    queryKey: platformBillingKeys.approvalDetail(tenantId, reviewId),
    queryFn: ({ signal }) => getPlatformPaymentApprovalDetail(tenantId, reviewId, signal),
    enabled: enabled && tenantId.length > 0 && reviewId.length > 0,
    staleTime: 0,
    gcTime: 0,
  });
}

export function usePlatformPaymentAdjustmentQueue(
  tenantId: string,
  page: number,
  pageSize: number,
  enabled: boolean,
) {
  return useQuery({
    queryKey: platformBillingKeys.adjustmentQueue(tenantId, page, pageSize),
    queryFn: ({ signal }) => listPlatformPaymentAdjustmentQueue(tenantId, page, pageSize, signal),
    enabled: enabled && tenantId.length > 0,
    placeholderData: keepPreviousData,
    staleTime: 5_000,
  });
}

export function usePlatformPaymentSubmissions(
  tenantId: string,
  page: number,
  pageSize: number,
  enabled: boolean,
) {
  return useQuery({
    queryKey: platformBillingKeys.submissionQueue(tenantId, page, pageSize),
    queryFn: ({ signal }) => listPlatformPaymentSubmissions(tenantId, page, pageSize, signal),
    enabled: enabled && tenantId.length > 0,
    placeholderData: keepPreviousData,
    staleTime: 5_000,
  });
}

export function usePlatformPaymentSubmissionDetail(
  tenantId: string,
  submissionId: string,
  enabled: boolean,
) {
  return useQuery({
    queryKey: platformBillingKeys.submissionDetail(tenantId, submissionId),
    queryFn: ({ signal }) => getPlatformPaymentSubmission(tenantId, submissionId, signal),
    enabled: enabled && tenantId.length > 0 && submissionId.length > 0,
    staleTime: 0,
    gcTime: 0,
  });
}

function usePricingMutation<TVariables>(mutationFn: (variables: TVariables) => Promise<unknown>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: platformBillingKeys.all }),
  });
}

export function useCreatePlatformPricingPlan() {
  return usePricingMutation((payload: PricingPlanCreate) => createPlatformPricingPlan(payload));
}

export function useCreatePlatformPricingPrice() {
  return usePricingMutation(
    ({ planId, payload }: { planId: string; payload: PricingPriceDraftCreate }) =>
      createPlatformPricingPrice(planId, payload),
  );
}

export function useSchedulePlatformPricingPrice() {
  return usePricingMutation(({ priceId, payload }: { priceId: string; payload: PricingSchedule }) =>
    schedulePlatformPricingPrice(priceId, payload),
  );
}

export function useActivatePlatformPricingPrice() {
  return usePricingMutation(({ priceId, payload }: { priceId: string; payload: PricingActivate }) =>
    activatePlatformPricingPrice(priceId, payload),
  );
}

export function useCancelPlatformPricingPrice() {
  return usePricingMutation(({ priceId, payload }: { priceId: string; payload: PricingCancel }) =>
    cancelPlatformPricingPrice(priceId, payload),
  );
}

export function useCreatePlatformBankPaymentReview() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      tenantId,
      payload,
    }: {
      tenantId: string;
      payload: PlatformBankPaymentReviewCreate;
    }) => createPlatformBankPaymentReview(tenantId, payload),
    onSuccess: async (_result, variables) => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: platformBillingKeys.financialAccount(variables.tenantId),
        }),
        queryClient.invalidateQueries({
          queryKey: [...platformBillingKeys.all, "v2", "approval-queue", variables.tenantId],
        }),
      ]);
    },
  });
}

export function useApprovePlatformBankPayment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      tenantId,
      reviewId,
      payload,
    }: {
      tenantId: string;
      reviewId: string;
      payload: PlatformBankPaymentApprove;
    }) => approvePlatformBankPayment(tenantId, reviewId, payload),
    onSuccess: async (_result, variables) => {
      queryClient.removeQueries({
        queryKey: platformBillingKeys.approvalDetail(variables.tenantId, variables.reviewId),
      });
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: platformBillingKeys.financialAccount(variables.tenantId),
        }),
        queryClient.invalidateQueries({
          queryKey: [...platformBillingKeys.all, "v2", "approval-queue", variables.tenantId],
        }),
      ]);
    },
  });
}

export function useRejectPlatformBankPaymentReview() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      tenantId,
      reviewId,
      payload,
    }: {
      tenantId: string;
      reviewId: string;
      payload: PlatformBankPaymentReviewReject;
    }) => rejectPlatformBankPaymentReview(tenantId, reviewId, payload),
    onSuccess: async (_result, variables) => {
      queryClient.removeQueries({
        queryKey: platformBillingKeys.approvalDetail(variables.tenantId, variables.reviewId),
      });
      await queryClient.invalidateQueries({
        queryKey: [...platformBillingKeys.all, "v2", "approval-queue", variables.tenantId],
      });
    },
  });
}

export function useReviewPlatformPaymentSubmission() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      tenantId,
      submissionId,
      payload,
    }: {
      tenantId: string;
      submissionId: string;
      payload: PlatformPaymentSubmissionReview;
    }) => reviewPlatformPaymentSubmission(tenantId, submissionId, payload),
    onSuccess: async (_result, variables) => {
      queryClient.removeQueries({
        queryKey: platformBillingKeys.submissionDetail(
          variables.tenantId,
          variables.submissionId,
        ),
      });
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: [...platformBillingKeys.all, "v2", "submission-queue", variables.tenantId],
        }),
        queryClient.invalidateQueries({
          queryKey: [...platformBillingKeys.all, "v2", "approval-queue", variables.tenantId],
        }),
      ]);
    },
  });
}

export function useRejectPlatformPaymentSubmission() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      tenantId,
      submissionId,
      payload,
    }: {
      tenantId: string;
      submissionId: string;
      payload: PlatformPaymentSubmissionReject;
    }) => rejectPlatformPaymentSubmission(tenantId, submissionId, payload),
    onSuccess: async (_result, variables) => {
      queryClient.removeQueries({
        queryKey: platformBillingKeys.submissionDetail(
          variables.tenantId,
          variables.submissionId,
        ),
      });
      await queryClient.invalidateQueries({
        queryKey: [...platformBillingKeys.all, "v2", "submission-queue", variables.tenantId],
      });
    },
  });
}

export function useCreatePlatformPaymentAdjustment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      tenantId,
      paymentId,
      payload,
    }: {
      tenantId: string;
      paymentId: string;
      payload: PlatformPaymentAdjustmentCreate;
    }) => createPlatformPaymentAdjustment(tenantId, paymentId, payload),
    onSuccess: async (_result, variables) => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: platformBillingKeys.financialAccount(variables.tenantId),
        }),
        queryClient.invalidateQueries({
          queryKey: [...platformBillingKeys.all, "v2", "adjustment-queue", variables.tenantId],
        }),
      ]);
    },
  });
}

export function useApprovePlatformPaymentAdjustment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      tenantId,
      adjustmentId,
      payload,
    }: {
      tenantId: string;
      adjustmentId: string;
      payload: PlatformPaymentAdjustmentApprove;
    }) => approvePlatformPaymentAdjustment(tenantId, adjustmentId, payload),
    onSuccess: async (_result, variables) => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: platformBillingKeys.financialAccount(variables.tenantId),
        }),
        queryClient.invalidateQueries({
          queryKey: [...platformBillingKeys.all, "v2", "adjustment-queue", variables.tenantId],
        }),
      ]);
    },
  });
}

export function useRejectPlatformPaymentAdjustment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      tenantId,
      adjustmentId,
      payload,
    }: {
      tenantId: string;
      adjustmentId: string;
      payload: PlatformPaymentAdjustmentReject;
    }) => rejectPlatformPaymentAdjustment(tenantId, adjustmentId, payload),
    onSuccess: async (_result, variables) => {
      await queryClient.invalidateQueries({
        queryKey: [...platformBillingKeys.all, "v2", "adjustment-queue", variables.tenantId],
      });
    },
  });
}
