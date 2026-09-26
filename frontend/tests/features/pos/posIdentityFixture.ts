import { type MeResponse } from "@/features/auth/types";
import { type PosStorageScope } from "@/features/pos/posStorageScope";
import { useAuthStore } from "@/stores/auth";

export const POS_SCOPE: PosStorageScope = { tenantId: "t-1", userId: "u-1" };

export function authenticatePos(scope = POS_SCOPE): MeResponse {
  const user: MeResponse = {
    id: scope.userId,
    active_tenant_id: scope.tenantId,
    home_tenant_id: scope.tenantId,
    email: "cashier@example.test",
    full_name: "Кассир",
    is_developer: false,
    is_administrator: false,
    status: "active",
    last_login_at: null,
    level: 1,
    is_tenant_owner: false,
    branch_assignments: {},
    permissions: ["pos.sell"],
    platform_capabilities: [],
    support_access: null,
  };
  useAuthStore.getState().setUser(user);
  return user;
}
