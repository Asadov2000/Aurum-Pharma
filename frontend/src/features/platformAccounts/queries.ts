import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  activatePlatformStaffAccount,
  invitePlatformStaffAccount,
  listPlatformStaffAccounts,
} from "./api";
import {
  type PlatformStaffAccountFilters,
  type PlatformStaffActivationPayload,
  type PlatformStaffInvitationPayload,
} from "./types";

export const platformAccountsKeys = {
  all: ["platform-accounts"] as const,
  list: (filters: PlatformStaffAccountFilters) =>
    [...platformAccountsKeys.all, "list", filters] as const,
};

export function usePlatformStaffAccounts(filters: PlatformStaffAccountFilters) {
  return useQuery({
    queryKey: platformAccountsKeys.list(filters),
    queryFn: () => listPlatformStaffAccounts(filters),
    staleTime: 15_000,
  });
}

export function useInvitePlatformStaffAccount() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: PlatformStaffInvitationPayload) => invitePlatformStaffAccount(payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: platformAccountsKeys.all });
    },
  });
}

export function useActivatePlatformStaffAccount() {
  return useMutation({
    mutationFn: (payload: PlatformStaffActivationPayload) => activatePlatformStaffAccount(payload),
  });
}
