import { api } from "@/lib/api";

import {
  type PlatformBankPaymentApprove,
  type PlatformBankPaymentReviewCommandResult,
  type PlatformBankPaymentReviewCreate,
  type PlatformBillingOverview,
  type PlatformFinancialAccount,
  type PlatformInvoiceFilters,
  type PlatformInvoiceList,
  type PlatformPaymentApprovalCommandResult,
  type PlatformPricingPlanCommandResult,
  type PlatformPricingPlanList,
  type PlatformPricingVersionCommandResult,
  type PricingActivate,
  type PricingCancel,
  type PricingPlanCreate,
  type PricingPriceDraftCreate,
  type PricingSchedule,
} from "./types";

export async function getPlatformBillingOverview(signal?: AbortSignal) {
  const { data } = await api.get<PlatformBillingOverview>("/admin/billing/overview", { signal });
  return data;
}

export async function listPlatformInvoices(filters: PlatformInvoiceFilters, signal?: AbortSignal) {
  const { data } = await api.get<PlatformInvoiceList>("/admin/billing/invoices", {
    params: filters,
    signal,
  });
  return data;
}

export async function listPlatformPricingPlans(
  page: number,
  pageSize: number,
  signal?: AbortSignal,
) {
  const { data } = await api.get<PlatformPricingPlanList>("/admin/billing/plans", {
    params: { page, page_size: pageSize },
    signal,
  });
  return data;
}

export async function createPlatformPricingPlan(payload: PricingPlanCreate) {
  const { data } = await api.post<PlatformPricingPlanCommandResult>(
    "/admin/billing/plans",
    payload,
  );
  return data;
}

export async function createPlatformPricingPrice(planId: string, payload: PricingPriceDraftCreate) {
  const { data } = await api.post<PlatformPricingVersionCommandResult>(
    `/admin/billing/plans/${planId}/prices`,
    payload,
  );
  return data;
}

export async function schedulePlatformPricingPrice(priceId: string, payload: PricingSchedule) {
  const { data } = await api.post<PlatformPricingVersionCommandResult>(
    `/admin/billing/prices/${priceId}/schedule`,
    payload,
  );
  return data;
}

export async function activatePlatformPricingPrice(priceId: string, payload: PricingActivate) {
  const { data } = await api.post<PlatformPricingVersionCommandResult>(
    `/admin/billing/prices/${priceId}/activate`,
    payload,
  );
  return data;
}

export async function cancelPlatformPricingPrice(priceId: string, payload: PricingCancel) {
  const { data } = await api.post<PlatformPricingVersionCommandResult>(
    `/admin/billing/prices/${priceId}/cancel`,
    payload,
  );
  return data;
}

export async function getPlatformFinancialAccount(tenantId: string, signal?: AbortSignal) {
  const { data } = await api.get<PlatformFinancialAccount>(
    `/admin/billing/tenants/${tenantId}/financial-account`,
    { signal },
  );
  return data;
}

export async function createPlatformBankPaymentReview(
  tenantId: string,
  payload: PlatformBankPaymentReviewCreate,
) {
  const { data } = await api.post<PlatformBankPaymentReviewCommandResult>(
    `/admin/billing/tenants/${tenantId}/payment-reviews`,
    payload,
  );
  return data;
}

export async function approvePlatformBankPayment(
  tenantId: string,
  reviewId: string,
  payload: PlatformBankPaymentApprove,
) {
  const { data } = await api.post<PlatformPaymentApprovalCommandResult>(
    `/admin/billing/tenants/${tenantId}/payment-reviews/${reviewId}/approve`,
    payload,
  );
  return data;
}
