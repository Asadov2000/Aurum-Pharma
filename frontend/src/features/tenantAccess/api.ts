import { api } from "@/lib/api";
import { type TenantAccessPolicy } from "./types";

export async function getTenantAccessPolicy(signal: AbortSignal): Promise<TenantAccessPolicy> {
  const { data } = await api.get<TenantAccessPolicy>("/tenant/access-policy", { signal });
  return data;
}
