import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { getPlatformBillingOverview, listPlatformInvoices } from "./api";
import { type PlatformInvoiceFilters } from "./types";

export const platformBillingKeys = {
  all: ["platform-billing"] as const,
  overview: () => [...platformBillingKeys.all, "overview"] as const,
  invoices: (filters: PlatformInvoiceFilters) =>
    [...platformBillingKeys.all, "invoices", filters] as const,
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
