import { useAuthStore } from "@/stores/auth";

export function useFilterPreferenceKey(screen: string): string {
  const user = useAuthStore((state) => state.user);
  const userId = user?.id ?? "anonymous";
  const tenantId =
    user?.support_access?.tenant_id ?? user?.active_tenant_id ?? user?.home_tenant_id ?? "platform";

  return `${userId}:${tenantId}:${screen}`;
}
