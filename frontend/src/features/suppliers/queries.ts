import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createSupplier,
  createSupplierReturn,
  listSupplierReturns,
  listSuppliers,
  updateSupplier,
} from "./api";
import {
  type SupplierCreatePayload,
  type SupplierReturnCreatePayload,
  type SupplierUpdatePayload,
} from "./types";

export const suppliersKeys = {
  list: (includeInactive: boolean) => ["suppliers", "list", { includeInactive }] as const,
  returns: (params: { supplier_id?: string; date_from?: string; date_to?: string }) =>
    ["suppliers", "returns", params] as const,
};

export function useSuppliersQuery(includeInactive: boolean, enabled = true) {
  return useQuery({
    queryKey: suppliersKeys.list(includeInactive),
    queryFn: () => listSuppliers(includeInactive),
    enabled,
  });
}

export function useCreateSupplier() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: SupplierCreatePayload) => createSupplier(payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["suppliers", "list"] });
    },
  });
}

export function useUpdateSupplier() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { id: string; payload: SupplierUpdatePayload }) =>
      updateSupplier(args.id, args.payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["suppliers", "list"] });
    },
  });
}

export function useSupplierReturnsQuery(
  params: { supplier_id?: string; date_from?: string; date_to?: string },
  enabled = true,
) {
  return useQuery({
    queryKey: suppliersKeys.returns(params),
    queryFn: () => listSupplierReturns(params),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useCreateSupplierReturn() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: SupplierReturnCreatePayload) => createSupplierReturn(payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["suppliers", "returns"] });
      void qc.invalidateQueries({ queryKey: ["inventory", "batches"] });
    },
  });
}
