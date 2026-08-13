import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  activatePlatformPricingPrice,
  cancelPlatformPricingPrice,
  createPlatformPricingPlan,
  createPlatformPricingPrice,
  getPlatformBillingOverview,
  listPlatformInvoices,
  listPlatformPricingPlans,
  schedulePlatformPricingPrice,
} from "./api";
import {
  type PlatformInvoiceFilters,
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
