import { useQuery } from "@tanstack/react-query";

import { activeTenantId } from "@/features/auth/tenantContext";
import { useAuthStore } from "@/stores/auth";

import { fetchPersonalShifts } from "./api";

export function usePersonalShiftsQuery(page: number) {
  const user = useAuthStore((state) => state.user);
  const authenticated = useAuthStore((state) => state.accessToken !== null);
  return useQuery({
    queryKey: [
      "my-work",
      user?.id ?? null,
      activeTenantId(user),
      user?.support_access?.id ?? null,
      page,
    ],
    queryFn: ({ signal }) => fetchPersonalShifts(page, signal),
    enabled:
      authenticated &&
      user !== null &&
      activeTenantId(user) !== null &&
      !user.is_developer &&
      !user.is_administrator &&
      !user.support_access,
    staleTime: 30_000,
    refetchOnMount: "always",
  });
}
