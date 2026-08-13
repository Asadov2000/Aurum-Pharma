import { api } from "@/lib/api";

import {
  type PlatformBillingOverview,
  type PlatformInvoiceFilters,
  type PlatformInvoiceList,
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
