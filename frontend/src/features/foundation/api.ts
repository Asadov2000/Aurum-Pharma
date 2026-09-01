import { api } from "@/lib/api";

import {
  type Branch,
  type BranchCreatePayload,
  type BranchLifecycleImpact,
  type BranchListResponse,
  type BranchSearchParams,
  type BranchUpdatePayload,
  type OwnerCreatePayload,
  type OwnerProvision,
  type Register,
  type RegisterCreatePayload,
  type RegisterListResponse,
  type RegisterSearchParams,
  type RegisterUpdatePayload,
  type Tenant,
  type TenantCreatePayload,
  type TenantMemberCreatePayload,
  type TenantMembership,
  type TenantOperationalSettings,
  type TenantSettings,
  type TenantSettingsUpdatePayload,
  type TenantUpdatePayload,
} from "./types";
import { normalizePosPaymentMethods } from "./paymentSettings";

type TenantSettingsResponse = Omit<
  TenantSettings,
  "pos_payment_methods" | "pos_mixed_payment_enabled"
> &
  Partial<Pick<TenantSettings, "pos_payment_methods" | "pos_mixed_payment_enabled">>;

function normalizeTenantSettings(data: TenantSettingsResponse): TenantSettings {
  return {
    ...data,
    pos_payment_methods: normalizePosPaymentMethods(data.pos_payment_methods),
    pos_mixed_payment_enabled: data.pos_mixed_payment_enabled ?? true,
  };
}

// ---- Tenants (admin) -------------------------------------------------------

export async function listTenants(): Promise<Tenant[]> {
  // Server supports limit/offset (no search); fetch a wide page and let the UI
  // search + paginate on the client. 500 is the backend's max.
  const { data } = await api.get<Tenant[]>("/admin/tenants", { params: { limit: 500 } });
  return data;
}

export async function createTenant(payload: TenantCreatePayload): Promise<Tenant> {
  const { data } = await api.post<Tenant>("/admin/tenants", payload);
  return data;
}

export async function updateTenant(
  tenantId: string,
  payload: TenantUpdatePayload,
): Promise<Tenant> {
  const { data } = await api.patch<Tenant>(`/admin/tenants/${tenantId}`, payload);
  return data;
}

export async function createTenantOwner(
  tenantId: string,
  payload: OwnerCreatePayload,
): Promise<OwnerProvision> {
  const { data } = await api.post<OwnerProvision>(`/admin/tenants/${tenantId}/owner`, payload);
  return data;
}

export async function createTenantMember(
  tenantId: string,
  payload: TenantMemberCreatePayload,
): Promise<TenantMembership> {
  const { data } = await api.post<TenantMembership>(`/admin/tenants/${tenantId}/members`, payload);
  return data;
}

// ---- Tenant settings -------------------------------------------------------

export async function getTenantSettings(): Promise<TenantSettings> {
  const { data } = await api.get<TenantSettingsResponse>("/tenant/settings");
  return normalizeTenantSettings(data);
}

export async function updateTenantSettings(
  payload: TenantSettingsUpdatePayload,
): Promise<TenantSettings> {
  const { data } = await api.patch<TenantSettingsResponse>("/tenant/settings", payload);
  return normalizeTenantSettings(data);
}

export async function getTenantOperationalSettings(): Promise<TenantOperationalSettings> {
  const { data } = await api.get<TenantOperationalSettings>("/tenant/operational-settings");
  return {
    ...data,
    pos_payment_methods: normalizePosPaymentMethods(data.pos_payment_methods),
  };
}

// ---- Branches --------------------------------------------------------------

export async function listBranches(includeInactive = false): Promise<Branch[]> {
  const { data } = await api.get<Branch[]>("/branches", {
    params: { include_inactive: includeInactive },
  });
  return data;
}

export async function searchBranches(
  params: BranchSearchParams,
  signal?: AbortSignal,
): Promise<BranchListResponse> {
  const { data } = await api.post<BranchListResponse>(
    "/branches/search",
    {
      q: params.q || undefined,
      branch_type: params.branch_type || undefined,
      is_active: params.is_active,
      page: params.page ?? 1,
      page_size: params.page_size ?? 25,
    },
    { signal },
  );
  return data;
}

export async function createBranch(payload: BranchCreatePayload): Promise<Branch> {
  const { data } = await api.post<Branch>("/branches", payload);
  return data;
}

export async function updateBranch(
  branchId: string,
  payload: BranchUpdatePayload,
): Promise<Branch> {
  const { data } = await api.patch<Branch>(`/branches/${branchId}`, payload);
  return data;
}

export async function deleteBranch(branchId: string): Promise<Branch> {
  const { data } = await api.delete<Branch>(`/branches/${branchId}`);
  return data;
}

export async function getBranchLifecycleImpact(branchId: string): Promise<BranchLifecycleImpact> {
  const { data } = await api.get<BranchLifecycleImpact>(`/branches/${branchId}/lifecycle-impact`);
  return data;
}

// ---- Registers -------------------------------------------------------------

export async function listRegisters(
  branchId?: string | null,
  includeInactive = false,
): Promise<Register[]> {
  const { data } = await api.get<Register[]>("/registers", {
    params: {
      ...(branchId ? { branch_id: branchId } : {}),
      include_inactive: includeInactive,
    },
  });
  return data;
}

export async function searchRegisters(
  params: RegisterSearchParams,
  signal?: AbortSignal,
): Promise<RegisterListResponse> {
  const { data } = await api.post<RegisterListResponse>(
    "/registers/search",
    {
      q: params.q || undefined,
      branch_id: params.branch_id || undefined,
      printer_type: params.printer_type || undefined,
      is_active: params.is_active,
      page: params.page ?? 1,
      page_size: params.page_size ?? 25,
    },
    { signal },
  );
  return data;
}

export async function createRegister(payload: RegisterCreatePayload): Promise<Register> {
  const { data } = await api.post<Register>("/registers", payload);
  return data;
}

export async function updateRegister(
  registerId: string,
  payload: RegisterUpdatePayload,
): Promise<Register> {
  const { data } = await api.patch<Register>(`/registers/${registerId}`, payload);
  return data;
}

export async function deleteRegister(registerId: string): Promise<Register> {
  const { data } = await api.delete<Register>(`/registers/${registerId}`);
  return data;
}
