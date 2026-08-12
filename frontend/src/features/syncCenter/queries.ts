import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { getSyncMonitoringOverview } from "./api";
import { type SyncMonitoringFilters } from "./types";

const REFRESH_INTERVAL_MS = 60_000;

export const syncMonitoringKeys = {
  all: ["sync-monitoring"] as const,
  overview: (filters: SyncMonitoringFilters) =>
    [...syncMonitoringKeys.all, "overview", filters] as const,
};

export function useSyncMonitoringOverview(filters: SyncMonitoringFilters, enabled: boolean) {
  const isPageVisible = usePageVisibility();

  return useQuery({
    queryKey: syncMonitoringKeys.overview(filters),
    queryFn: ({ signal }) => getSyncMonitoringOverview(filters, signal),
    enabled,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    refetchInterval: isPageVisible ? REFRESH_INTERVAL_MS : false,
    refetchIntervalInBackground: false,
  });
}

function usePageVisibility(): boolean {
  const [isVisible, setIsVisible] = useState(
    () => typeof document === "undefined" || document.visibilityState === "visible",
  );

  useEffect(() => {
    const updateVisibility = () => setIsVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", updateVisibility);
    return () => document.removeEventListener("visibilitychange", updateVisibility);
  }, []);

  return isVisible;
}
