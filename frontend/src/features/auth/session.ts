import { queryClient } from "@/lib/query";
import { useAuthStore } from "@/stores/auth";
import { useSupportAccessStore } from "@/stores/supportAccess";

export function clearClientSession(): void {
  queryClient.clear();
  useSupportAccessStore.getState().clear();
  useAuthStore.getState().clear();
}

export function clearClientSupportContext(): void {
  useSupportAccessStore.getState().clear();
  queryClient.clear();

  const state = useAuthStore.getState();
  const user = state.user;
  if (!user?.support_access) return;

  state.setUser({
    ...user,
    active_tenant_id: null,
    branch_assignments: {},
    is_tenant_owner: false,
    permissions: [],
    support_access: null,
  });
}
