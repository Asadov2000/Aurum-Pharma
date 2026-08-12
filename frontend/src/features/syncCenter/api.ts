import { api } from "@/lib/api";

import { type SyncMonitoringFilters, type SyncMonitoringOverview } from "./types";

export async function getSyncMonitoringOverview(
  filters: SyncMonitoringFilters,
  signal?: AbortSignal,
): Promise<SyncMonitoringOverview> {
  const { data } = await api.get<SyncMonitoringOverview>("/admin/sync/overview", {
    params: filters,
    signal,
  });
  return data;
}
