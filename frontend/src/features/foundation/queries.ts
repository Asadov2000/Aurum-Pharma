import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createBranch,
  createRegister,
  createTenant,
  createTenantOwner,
  deleteBranch,
  deleteRegister,
  getTenantSettings,
  listBranches,
  listRegisters,
  listTenants,
  updateBranch,
  updateRegister,
  updateTenant,
  updateTenantSettings,
} from "./api";
import {
  type BranchCreatePayload,
  type BranchUpdatePayload,
  type OwnerCreatePayload,
  type RegisterCreatePayload,
  type RegisterUpdatePayload,
  type TenantCreatePayload,
  type TenantSettingsUpdatePayload,
  type TenantUpdatePayload,
} from "./types";

export const foundationKeys = {
  tenants: ["foundation", "tenants"] as const,
  settings: ["foundation", "settings"] as const,
  branches: (includeInactive: boolean) =>
    ["foundation", "branches", { includeInactive }] as const,
  registers: (branchId: string | null, includeInactive: boolean) =>
    ["foundation", "registers", { branchId, includeInactive }] as const,
};

// Tenants

export function useTenantsQuery(enabled = true) {
  return useQuery({
    queryKey: foundationKeys.tenants,
    queryFn: listTenants,
    enabled,
  });
}

export function useCreateTenant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: TenantCreatePayload) => createTenant(payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: foundationKeys.tenants });
    },
  });
}

export function useUpdateTenant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { id: string; payload: TenantUpdatePayload }) =>
      updateTenant(args.id, args.payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: foundationKeys.tenants });
    },
  });
}

export function useCreateTenantOwner() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { tenantId: string; payload: OwnerCreatePayload }) =>
      createTenantOwner(args.tenantId, args.payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: foundationKeys.tenants });
    },
  });
}

// Settings

export function useTenantSettingsQuery(enabled = true) {
  return useQuery({
    queryKey: foundationKeys.settings,
    queryFn: getTenantSettings,
    enabled,
  });
}

export function useUpdateTenantSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: TenantSettingsUpdatePayload) => updateTenantSettings(payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: foundationKeys.settings });
    },
  });
}

// Branches

export function useBranchesQuery(includeInactive: boolean, enabled = true) {
  return useQuery({
    queryKey: foundationKeys.branches(includeInactive),
    queryFn: () => listBranches(includeInactive),
    enabled,
  });
}

export function useCreateBranch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: BranchCreatePayload) => createBranch(payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["foundation", "branches"] });
    },
  });
}

export function useUpdateBranch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { id: string; payload: BranchUpdatePayload }) =>
      updateBranch(args.id, args.payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["foundation", "branches"] });
    },
  });
}

export function useDeleteBranch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteBranch(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["foundation", "branches"] });
    },
  });
}

// Registers

export function useRegistersQuery(
  branchId: string | null,
  includeInactive: boolean,
  enabled = true,
) {
  return useQuery({
    queryKey: foundationKeys.registers(branchId, includeInactive),
    queryFn: () => listRegisters(branchId, includeInactive),
    enabled,
  });
}

export function useCreateRegister() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: RegisterCreatePayload) => createRegister(payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["foundation", "registers"] });
    },
  });
}

export function useUpdateRegister() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { id: string; payload: RegisterUpdatePayload }) =>
      updateRegister(args.id, args.payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["foundation", "registers"] });
    },
  });
}

export function useDeleteRegister() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteRegister(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["foundation", "registers"] });
    },
  });
}
