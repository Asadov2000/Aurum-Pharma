import { useQuery } from "@tanstack/react-query";

import { useAuth } from "@/features/auth/hooks";

import { getUserPreferences } from "./api";

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
