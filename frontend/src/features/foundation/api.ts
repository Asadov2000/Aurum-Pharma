import { api } from "@/lib/api";

import {
  type Branch,
  type BranchCreatePayload,
  type BranchUpdatePayload,
  type Register,
  type RegisterCreatePayload,
  type RegisterUpdatePayload,
  type Tenant,
  type TenantCreatePayload,
  type TenantSettings,
  type TenantSettingsUpdatePayload,
  type TenantUpdatePayload,
} from "./types";

// ---- Tenants (admin) -------------------------------------------------------

export async function listTenants(): Promise<Tenant[]> {
  const { data } = await api.get<Tenant[]>("/admin/tenants");
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

// ---- Tenant settings -------------------------------------------------------

export async function getTenantSettings(): Promise<TenantSettings> {
  const { data } = await api.get<TenantSettings>("/tenant/settings");
  return data;
}

export async function updateTenantSettings(
  payload: TenantSettingsUpdatePayload,
): Promise<TenantSettings> {
  const { data } = await api.patch<TenantSettings>("/tenant/settings", payload);
  return data;
}

// ---- Branches --------------------------------------------------------------

export async function listBranches(includeInactive = false): Promise<Branch[]> {
  const { data } = await api.get<Branch[]>("/branches", {
    params: { include_inactive: includeInactive },
  });
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
