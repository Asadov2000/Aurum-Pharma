import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useAuth } from "@/features/auth/hooks";

import { getUserPreferences, updateUserPreferences } from "./api";
import { type UserPreferences, type UserPreferencesUpdate } from "./types";

export const settingsKeys = {
  preferences: (userId: string | undefined, tenantId: string | null | undefined) =>
    ["settings", "preferences", userId ?? "anonymous", tenantId ?? "global"] as const,
  preferencesUpdate: ["settings", "preferences", "update"] as const,
};

export function useUserPreferencesQuery(enabled = true) {
  const { user } = useAuth();
  return useQuery({
    queryKey: settingsKeys.preferences(user?.id, user?.active_tenant_id),
    queryFn: getUserPreferences,
    enabled: enabled && user !== null,
    staleTime: 60_000,
  });
}

export function useUpdateUserPreferences() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const queryKey = settingsKeys.preferences(user?.id, user?.active_tenant_id);
  return useMutation({
    mutationKey: settingsKeys.preferencesUpdate,
    scope: {
      id: `user-preferences:${user?.id ?? "anonymous"}:${user?.active_tenant_id ?? "global"}`,
    },
    mutationFn: (payload: UserPreferencesUpdate) => {
      const cached = queryClient.getQueryData<UserPreferences>(queryKey);
      return updateUserPreferences({
        ...payload,
        expected_version: cached?.version ?? payload.expected_version,
      });
    },
    onSuccess: (data) => {
      queryClient.setQueryData(queryKey, data);
    },
  });
}
