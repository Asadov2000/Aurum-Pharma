import { api } from "@/lib/api";

import {
  type PlatformBankPaymentApprove,
  type PlatformBankPaymentReviewReject,
  type PlatformBankPaymentReviewCommandResult,
  type PlatformBankPaymentReviewCreate,
  type PlatformBillingOverview,
  type PlatformBillingTenantFilters,
  type PlatformBillingTenantList,
  type PlatformFinancialAccount,
  type PlatformInvoiceFilters,
  type PlatformInvoiceList,
  type PlatformPaymentApprovalCommandResult,
  type PlatformPaymentApprovalQueue,
  type PlatformPaymentAdjustmentApprovalCommandResult,
  type PlatformPaymentAdjustmentApprove,
  type PlatformPaymentAdjustmentCreate,
  type PlatformPaymentAdjustmentQueue,
  type PlatformPaymentAdjustmentReject,
  type PlatformPaymentAdjustmentRejectionCommandResult,
  type PlatformPaymentAdjustmentRequestCommandResult,
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

export async function listPlatformBillingTenants(
  filters: PlatformBillingTenantFilters,
  signal?: AbortSignal,
) {
  const { data } = await api.get<PlatformBillingTenantList>("/admin/billing/tenants", {
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

export async function listPlatformPaymentApprovalQueue(
  tenantId: string,
  page: number,
  pageSize: number,
  signal?: AbortSignal,
) {
  const { data } = await api.get<PlatformPaymentApprovalQueue>(
    `/admin/billing/tenants/${tenantId}/payment-reviews`,
    { params: { page, page_size: pageSize }, signal },
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

export async function rejectPlatformBankPaymentReview(
  tenantId: string,
  reviewId: string,
  payload: PlatformBankPaymentReviewReject,
) {
  const { data } = await api.post<PlatformBankPaymentReviewCommandResult>(
    `/admin/billing/tenants/${tenantId}/payment-reviews/${reviewId}/reject`,
    payload,
  );
  return data;
}

export async function createPlatformPaymentAdjustment(
  tenantId: string,
  paymentId: string,
  payload: PlatformPaymentAdjustmentCreate,
) {
  const { data } = await api.post<PlatformPaymentAdjustmentRequestCommandResult>(
    `/admin/billing/tenants/${tenantId}/payments/${paymentId}/adjustments`,
    payload,
  );
  return data;
}

export async function listPlatformPaymentAdjustmentQueue(
  tenantId: string,
  page: number,
  pageSize: number,
  signal?: AbortSignal,
) {
  const { data } = await api.get<PlatformPaymentAdjustmentQueue>(
    `/admin/billing/tenants/${tenantId}/payment-adjustments`,
    { params: { page, page_size: pageSize }, signal },
  );
  return data;
}

export async function approvePlatformPaymentAdjustment(
  tenantId: string,
  adjustmentId: string,
  payload: PlatformPaymentAdjustmentApprove,
) {
  const { data } = await api.post<PlatformPaymentAdjustmentApprovalCommandResult>(
    `/admin/billing/tenants/${tenantId}/payment-adjustments/${adjustmentId}/approve`,
    payload,
  );
  return data;
}

export async function rejectPlatformPaymentAdjustment(
  tenantId: string,
  adjustmentId: string,
  payload: PlatformPaymentAdjustmentReject,
) {
  const { data } = await api.post<PlatformPaymentAdjustmentRejectionCommandResult>(
    `/admin/billing/tenants/${tenantId}/payment-adjustments/${adjustmentId}/reject`,
    payload,
  );
  return data;
}
