import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { listCustomerReturns, listPhysicalCustomerReturns, resolveCustomerReturn } from "./api";
import {
  type CustomerReturnSearchParams,
  type PhysicalCustomerReturnSearchParams,
  type ResolveCustomerReturnPayload,
} from "./types";

export const customerReturnsKeys = {
  all: ["customer-returns"] as const,
  list: (params: CustomerReturnSearchParams) => ["customer-returns", "list", params] as const,
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

export function useResolveCustomerReturn() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: ResolveCustomerReturnPayload }) =>
      resolveCustomerReturn(id, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: customerReturnsKeys.all }),
  });
}
