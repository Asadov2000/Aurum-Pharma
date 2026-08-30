import { api } from "@/lib/api";

import { type DashboardSummary } from "./types";

export async function getDashboardSummary(forceRefresh = false): Promise<DashboardSummary> {
  const { data } = await api.get<DashboardSummary>("/dashboard/summary", {
    params: forceRefresh ? { refresh: true } : undefined,
  });
  return data;
}
