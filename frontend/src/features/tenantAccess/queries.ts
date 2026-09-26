import { useQuery } from "@tanstack/react-query";
import { getTenantAccessPolicy } from "./api";

export const tenantAccessKeys = {
  policy: (userId: string | undefined, tenantId: string | null) =>
    ["tenant-access", userId, tenantId] as const,
};
export function useTenantAccessPolicy(
  userId: string | undefined,
  tenantId: string | null,
  enabled: boolean,
) {
  return useQuery({
    queryKey: tenantAccessKeys.policy(userId, tenantId),
    queryFn: ({ signal }) => getTenantAccessPolicy(signal),
    enabled,
    staleTime: 0,
    refetchOnWindowFocus: true,
    refetchInterval: enabled ? 60_000 : false,
    retry: false,
  });
}
