import { api } from "@/lib/api";

import {
  type PlatformBillingOverview,
  type PlatformInvoiceFilters,
  type PlatformInvoiceList,
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
