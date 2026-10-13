import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { permissionBranchScope } from "@/features/auth/branchPermissions";
import { useAuth } from "@/features/auth/hooks";
import { hasPermission } from "@/features/auth/permissions";

import {
  listCustomerReturns,
  listMixedCustomerReturns,
  listPhysicalCustomerReturns,
  resolveCustomerReturn,
} from "./api";
import {
  type CustomerReturnSearchParams,
  type PhysicalCustomerReturnSearchParams,
  type MixedCustomerReturnSearchParams,
  type ResolveCustomerReturnPayload,
} from "./types";

export const customerReturnsKeys = {
  all: ["customer-returns"] as const,
  list: (params: CustomerReturnSearchParams) => ["customer-returns", "list", params] as const,
  mixed: (params: MixedCustomerReturnSearchParams) =>
    ["customer-returns", "mixed", params] as const,
  physical: (params: PhysicalCustomerReturnSearchParams) =>
    ["customer-returns", "physical", params] as const,
};

export function useCustomerReturnsQuery(params: CustomerReturnSearchParams) {
  return useQuery({
    queryKey: customerReturnsKeys.list(params),
    queryFn: () => listCustomerReturns(params),
    placeholderData: keepPreviousData,
  });
}

export function usePhysicalCustomerReturnsQuery(params: PhysicalCustomerReturnSearchParams) {
  return useQuery({
    queryKey: customerReturnsKeys.physical(params),
    queryFn: () => listPhysicalCustomerReturns(params),
    placeholderData: keepPreviousData,
  });
}

export function useMixedCustomerReturnsQuery(params: MixedCustomerReturnSearchParams) {
  const { user } = useAuth();
  return useQuery({
    queryKey: [
      ...customerReturnsKeys.mixed(params),
      user?.active_tenant_id,
      user?.id,
      permissionBranchScope(user, "customer_returns.view"),
    ],
    queryFn: () => listMixedCustomerReturns(params),
    enabled: hasPermission(user, "customer_returns.view"),
  });
}

export function useResolveCustomerReturn() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: ResolveCustomerReturnPayload }) =>
      resolveCustomerReturn(id, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: customerReturnsKeys.all }),
  });
}
