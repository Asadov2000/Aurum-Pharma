import { useQuery } from "@tanstack/react-query";

import { getDashboardSummary } from "./api";

export const dashboardKeys = {
  summary: ["dashboard", "summary"] as const,
};

export function useDashboardSummary(enabled = true) {
  return useQuery({
    queryKey: dashboardKeys.summary,
    queryFn: getDashboardSummary,
    // Backend caches for 60s; keep the client roughly in step.
    staleTime: 60_000,
    enabled,
  });
}
