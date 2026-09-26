import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

export const ACCESS_MODULES = [
  "catalog",
  "incoming",
  "inventory",
  "suppliers",
  "pos",
  "staff",
  "settings",
] as const;
export type AccessModule = (typeof ACCESS_MODULES)[number];
export interface AccessPolicy {
  mode: "full" | "readonly";
  blocked_modules: AccessModule[];
  max_branches: number | null;
  max_users: number | null;
  max_registers: number | null;
  expires_at: string | null;
  reason: string;
  version: number;
}
export type AccessPolicyUpdate = Omit<AccessPolicy, "version"> & { expected_version: number };
export const accessPolicyKey = (tenantId: string) => ["tenant-access-policy", tenantId] as const;
export function useAccessPolicy(tenantId: string) {
  return useQuery({
    queryKey: accessPolicyKey(tenantId),
    queryFn: async () =>
      (await api.get<AccessPolicy>(`/admin/tenants/${tenantId}/access-policy`)).data,
  });
}
export function useUpdateAccessPolicy(tenantId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (payload: AccessPolicyUpdate) =>
      (await api.patch<AccessPolicy>(`/admin/tenants/${tenantId}/access-policy`, payload)).data,
    retry: false,
    onSuccess: (data) => {
      client.setQueryData(accessPolicyKey(tenantId), data);
      void client.invalidateQueries({ queryKey: ["foundation", "tenants"] });
    },
  });
}
