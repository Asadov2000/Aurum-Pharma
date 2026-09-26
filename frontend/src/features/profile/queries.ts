import { useQuery } from "@tanstack/react-query";

import { activeTenantId } from "@/features/auth/tenantContext";
import { useAuthStore } from "@/stores/auth";

import { fetchPersonalProfile } from "./api";

export function usePersonalProfileQuery() {
  const user = useAuthStore((state) => state.user);
  const authenticated = useAuthStore((state) => state.accessToken !== null);
  return useQuery({
    queryKey: [
      "auth",
      "profile",
      user?.id ?? null,
      activeTenantId(user),
      user?.support_access?.id ?? null,
    ],
    queryFn: ({ signal }) => fetchPersonalProfile(signal),
    enabled: authenticated && user !== null,
    staleTime: 30_000,
    refetchOnMount: "always",
  });
}
