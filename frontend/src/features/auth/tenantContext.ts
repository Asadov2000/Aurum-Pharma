import { type MeResponse } from "./types";

export function activeTenantId(user: MeResponse | null | undefined): string | null {
  if (!user) return null;
  if (user.active_tenant_id) return user.active_tenant_id;
  if (user.is_developer || user.is_administrator) return null;
  return user.home_tenant_id;
}
