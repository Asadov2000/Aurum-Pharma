import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import {
  getSyncMonitoringOverview,
  revokeSyncNode,
  startSyncCredentialRotation,
  transitionSyncCredentialRotation,
} from "./api";
import {
  type SyncCredentialRotationStartPayload,
  type SyncMonitoringFilters,
  type SyncNodeActionPayload,
} from "./types";

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

export function useStartSyncCredentialRotation(nodeId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: SyncCredentialRotationStartPayload) =>
      startSyncCredentialRotation(nodeId, payload),
    gcTime: 0,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: syncMonitoringKeys.all });
    },
  });
}

export function useTransitionSyncCredentialRotation(
  rotationId: string,
  action: "complete" | "cancel",
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: SyncNodeActionPayload) =>
      transitionSyncCredentialRotation(rotationId, action, payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: syncMonitoringKeys.all });
    },
  });
}

export function useRevokeSyncNode(nodeId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: SyncNodeActionPayload) => revokeSyncNode(nodeId, payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: syncMonitoringKeys.all });
    },
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
