import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { listCustomerReturns, resolveCustomerReturn } from "./api";
import { type CustomerReturnSearchParams, type ResolveCustomerReturnPayload } from "./types";

export const customerReturnsKeys = {
  all: ["customer-returns"] as const,
  list: (params: CustomerReturnSearchParams) => ["customer-returns", "list", params] as const,
};

export function useCustomerReturnsQuery(params: CustomerReturnSearchParams) {
  return useQuery({
    queryKey: customerReturnsKeys.list(params),
    queryFn: () => listCustomerReturns(params),
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
