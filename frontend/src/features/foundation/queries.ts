import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createBranch,
  createRegister,
  createTenant,
  createTenantMember,
  createTenantOwner,
  deleteBranch,
  deleteRegister,
  getTenantSettings,
  listBranches,
  listRegisters,
  listTenants,
  searchBranches,
  searchRegisters,
  updateBranch,
  updateRegister,
  updateTenant,
  updateTenantSettings,
} from "./api";
import {
  type BranchCreatePayload,
  type BranchSearchParams,
  type BranchUpdatePayload,
  type OwnerCreatePayload,
  type RegisterCreatePayload,
  type RegisterSearchParams,
  type RegisterUpdatePayload,
  type TenantCreatePayload,
  type TenantMemberCreatePayload,
  type TenantSettingsUpdatePayload,
  type TenantUpdatePayload,
} from "./types";

export const foundationKeys = {
  tenants: ["foundation", "tenants"] as const,
  tenantMembers: (tenantId: string) => ["foundation", "tenant-members", tenantId] as const,
  settings: ["foundation", "settings"] as const,
  branches: (includeInactive: boolean) => ["foundation", "branches", { includeInactive }] as const,
  branchSearch: (params: BranchSearchParams) => ["foundation", "branch-search", params] as const,
  registers: (branchId: string | null, includeInactive: boolean) =>
    ["foundation", "registers", { branchId, includeInactive }] as const,
  registerSearch: (params: RegisterSearchParams) =>
    ["foundation", "register-search", params] as const,
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

export function useCreateTenantMember() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { tenantId: string; payload: TenantMemberCreatePayload }) =>
      createTenantMember(args.tenantId, args.payload),
    onSuccess: async (_data, variables) => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: foundationKeys.tenants }),
        qc.invalidateQueries({ queryKey: foundationKeys.tenantMembers(variables.tenantId) }),
        qc.invalidateQueries({ queryKey: ["roles", "users"] }),
      ]);
    },
  });
}

// Settings

export function useTenantSettingsQuery(enabled = true, refetchOnWindowFocus = false) {
  return useQuery({
    queryKey: foundationKeys.settings,
    queryFn: getTenantSettings,
    enabled,
    refetchOnWindowFocus,
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

export function useBranchSearchQuery(params: BranchSearchParams, enabled = true) {
  return useQuery({
    queryKey: foundationKeys.branchSearch(params),
    queryFn: ({ signal }) => searchBranches(params, signal),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useCreateBranch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: BranchCreatePayload) => createBranch(payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["foundation", "branches"] });
      void qc.invalidateQueries({ queryKey: ["foundation", "branch-search"] });
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
      void qc.invalidateQueries({ queryKey: ["foundation", "branch-search"] });
    },
  });
}

export function useDeleteBranch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteBranch(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["foundation", "branches"] });
      void qc.invalidateQueries({ queryKey: ["foundation", "branch-search"] });
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

export function useRegisterSearchQuery(params: RegisterSearchParams, enabled = true) {
  return useQuery({
    queryKey: foundationKeys.registerSearch(params),
    queryFn: ({ signal }) => searchRegisters(params, signal),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useCreateRegister() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: RegisterCreatePayload) => createRegister(payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["foundation", "registers"] });
      void qc.invalidateQueries({ queryKey: ["foundation", "register-search"] });
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
      void qc.invalidateQueries({ queryKey: ["foundation", "register-search"] });
    },
  });
}

export function useDeleteRegister() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteRegister(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["foundation", "registers"] });
      void qc.invalidateQueries({ queryKey: ["foundation", "register-search"] });
    },
  });
}
