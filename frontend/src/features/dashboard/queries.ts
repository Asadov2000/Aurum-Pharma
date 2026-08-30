import { useQuery } from "@tanstack/react-query";
import { useRef } from "react";

import { getDashboardSummary } from "./api";

export const dashboardKeys = {
  summary: ["dashboard", "summary"] as const,
};

export function useDashboardSummary(enabled = true) {
  const forceRefresh = useRef(false);
  const query = useQuery({
    queryKey: dashboardKeys.summary,
    queryFn: () => getDashboardSummary(forceRefresh.current),
    // Backend caches for 60s; keep the client roughly in step.
    staleTime: 60_000,
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
    enabled,
  });

  const refresh = async (): Promise<void> => {
    forceRefresh.current = true;
    try {
      await query.refetch();
    } finally {
      forceRefresh.current = false;
    }
  };

  return {
    ...query,
    refresh,
  };
}
