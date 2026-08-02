import { api } from "@/lib/api";

import {
  type Supplier,
  type SupplierCreatePayload,
  type SupplierListResponse,
  type SupplierOptionList,
  type SupplierOptionSearchParams,
  type SupplierReturnCandidateList,
  type SupplierReturnCandidateSearchParams,
  type SupplierReturnCreatePayload,
  type SupplierReturnCreated,
  type SupplierReturnList,
  type SupplierReturnSearchParams,
  type SupplierSearchParams,
  type SupplierUpdatePayload,
} from "./types";

export async function listSuppliers(includeInactive = false): Promise<Supplier[]> {
  const { data } = await api.get<Supplier[]>("/suppliers", {
    params: { include_inactive: includeInactive },
  });
  return data;
}

export async function searchSuppliers(
  params: SupplierSearchParams,
  signal?: AbortSignal,
): Promise<SupplierListResponse> {
  const { data } = await api.post<SupplierListResponse>(
    "/suppliers/search",
    {
      q: params.q || undefined,
      is_active: params.is_active,
      page: params.page ?? 1,
      page_size: params.page_size ?? 25,
    },
    { signal },
  );
  return data;
}

export async function searchSupplierOptions(
  params: SupplierOptionSearchParams,
  signal?: AbortSignal,
): Promise<SupplierOptionList> {
  const { data } = await api.post<SupplierOptionList>(
    "/suppliers/options/search",
    {
      q: params.q || undefined,
      include_inactive: params.include_inactive ?? false,
      selected_id: params.selected_id || undefined,
      limit: params.limit ?? 20,
    },
    { signal },
  );
  return data;
}

export async function createSupplier(payload: SupplierCreatePayload): Promise<Supplier> {
  const { data } = await api.post<Supplier>("/suppliers", payload);
  return data;
}

export async function updateSupplier(
  id: string,
  payload: SupplierUpdatePayload,
): Promise<Supplier> {
  const { data } = await api.patch<Supplier>(`/suppliers/${id}`, payload);
  return data;
}

export async function searchSupplierReturns(
  params: SupplierReturnSearchParams,
  signal?: AbortSignal,
): Promise<SupplierReturnList> {
  const { data } = await api.post<SupplierReturnList>(
    "/suppliers/returns/search",
    {
      supplier_id: params.supplier_id || undefined,
      branch_id: params.branch_id || undefined,
      reason: params.reason || undefined,
      date_from: params.date_from || undefined,
      date_to: params.date_to || undefined,
      page: params.page ?? 1,
      page_size: params.page_size ?? 25,
    },
    { signal },
  );
  return data;
}

export async function searchSupplierReturnCandidates(
  params: SupplierReturnCandidateSearchParams,
  signal?: AbortSignal,
): Promise<SupplierReturnCandidateList> {
  const { data } = await api.post<SupplierReturnCandidateList>(
    "/suppliers/returns/candidates/search",
    {
      supplier_id: params.supplier_id,
      branch_id: params.branch_id || undefined,
      q: params.q || undefined,
      page: params.page ?? 1,
      page_size: params.page_size ?? 20,
    },
    { signal },
  );
  return data;
}

export async function createSupplierReturn(
  payload: SupplierReturnCreatePayload,
): Promise<SupplierReturnCreated> {
  const { data } = await api.post<SupplierReturnCreated>("/suppliers/returns", payload);
  return data;
}
