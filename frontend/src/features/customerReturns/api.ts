import { api } from "@/lib/api";

import {
  type CustomerReturnItem,
  type CustomerReturnList,
  type CustomerReturnSearchParams,
  type ResolveCustomerReturnPayload,
} from "./types";

export async function listCustomerReturns(
  params: CustomerReturnSearchParams,
): Promise<CustomerReturnList> {
  const { data } = await api.get<CustomerReturnList>("/customer-returns", {
    params: {
      status: params.status,
      branch_id: params.branch_id || undefined,
      search: params.search || undefined,
      page: params.page ?? 1,
      page_size: params.page_size ?? 25,
    },
  });
  return data;
}

export async function resolveCustomerReturn(
  id: string,
  payload: ResolveCustomerReturnPayload,
): Promise<CustomerReturnItem> {
  const { data } = await api.post<CustomerReturnItem>(`/customer-returns/${id}/resolve`, payload);
  return data;
}
