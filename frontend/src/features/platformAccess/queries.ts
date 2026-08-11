import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  approvePlatformAccessGrant,
  listPlatformAccessGrants,
  revokePlatformAccessGrant,
} from "./api";
import { type PlatformAccessActionPayload, type PlatformAccessGrantFilters } from "./types";

export const platformAccessKeys = {
  all: ["platform-access"] as const,
  grants: (filters: PlatformAccessGrantFilters) =>
    [...platformAccessKeys.all, "grants", filters] as const,
};

export function usePlatformAccessGrants(filters: PlatformAccessGrantFilters) {
  return useQuery({
    queryKey: platformAccessKeys.grants(filters),
    queryFn: () => listPlatformAccessGrants(filters),
    staleTime: 10_000,
  });
}

interface GrantMutationVariables {
  grantId: string;
  payload: PlatformAccessActionPayload;
}

export function useApprovePlatformAccessGrant() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ grantId, payload }: GrantMutationVariables) =>
      approvePlatformAccessGrant(grantId, payload),
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: platformAccessKeys.all });
    },
  });
}

export function useRevokePlatformAccessGrant() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ grantId, payload }: GrantMutationVariables) =>
      revokePlatformAccessGrant(grantId, payload),
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: platformAccessKeys.all });
    },
  });
}
