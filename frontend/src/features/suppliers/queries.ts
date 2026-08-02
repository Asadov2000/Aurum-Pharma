import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createSupplier,
  createSupplierReturn,
  listSuppliers,
  searchSupplierOptions,
  searchSupplierReturnCandidates,
  searchSupplierReturns,
  searchSuppliers,
  updateSupplier,
} from "./api";
import {
  type SupplierCreatePayload,
  type SupplierOptionSearchParams,
  type SupplierReturnCandidateSearchParams,
  type SupplierReturnCreatePayload,
  type SupplierReturnSearchParams,
  type SupplierSearchParams,
  type SupplierUpdatePayload,
} from "./types";

export const suppliersKeys = {
  list: (includeInactive: boolean) => ["suppliers", "list", { includeInactive }] as const,
  search: (params: SupplierSearchParams) => ["suppliers", "search", params] as const,
  options: (params: SupplierOptionSearchParams) => ["suppliers", "options", params] as const,
  returns: (params: SupplierReturnSearchParams) => ["suppliers", "returns", params] as const,
  returnCandidates: (params: SupplierReturnCandidateSearchParams) =>
    ["suppliers", "return-candidates", params] as const,
};

export function useSuppliersQuery(includeInactive: boolean, enabled = true) {
  return useQuery({
    queryKey: suppliersKeys.list(includeInactive),
    queryFn: () => listSuppliers(includeInactive),
    enabled,
  });
}

export function useSupplierSearchQuery(params: SupplierSearchParams, enabled = true) {
  return useQuery({
    queryKey: suppliersKeys.search(params),
    queryFn: ({ signal }) => searchSuppliers(params, signal),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useSupplierOptionsQuery(params: SupplierOptionSearchParams, enabled = true) {
  return useQuery({
    queryKey: suppliersKeys.options(params),
    queryFn: ({ signal }) => searchSupplierOptions(params, signal),
    enabled,
    staleTime: 30_000,
  });
}

export function useCreateSupplier() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: SupplierCreatePayload) => createSupplier(payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["suppliers"] });
    },
  });
}

export function useUpdateSupplier() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { id: string; payload: SupplierUpdatePayload }) =>
      updateSupplier(args.id, args.payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["suppliers"] });
    },
  });
}

export function useSupplierReturnsQuery(params: SupplierReturnSearchParams, enabled = true) {
  return useQuery({
    queryKey: suppliersKeys.returns(params),
    queryFn: ({ signal }) => searchSupplierReturns(params, signal),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useSupplierReturnCandidatesQuery(
  params: SupplierReturnCandidateSearchParams,
  enabled = true,
) {
  return useQuery({
    queryKey: suppliersKeys.returnCandidates(params),
    queryFn: ({ signal }) => searchSupplierReturnCandidates(params, signal),
    enabled,
  });
}

export function useCreateSupplierReturn() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: SupplierReturnCreatePayload) => createSupplierReturn(payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["suppliers", "returns"] });
      void qc.invalidateQueries({ queryKey: ["suppliers", "return-candidates"] });
      void qc.invalidateQueries({ queryKey: ["inventory", "batches"] });
    },
  });
}
