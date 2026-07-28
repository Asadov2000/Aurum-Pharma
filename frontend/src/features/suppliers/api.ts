import { api } from "@/lib/api";

import {
  type Supplier,
  type SupplierCreatePayload,
  type SupplierListResponse,
  type SupplierReturn,
  type SupplierReturnCreatePayload,
  type SupplierReturnCreated,
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

export async function listSupplierReturns(params: {
  supplier_id?: string;
  date_from?: string;
  date_to?: string;
}): Promise<SupplierReturn[]> {
  const { data } = await api.get<SupplierReturn[]>("/suppliers/returns", {
    params: {
      supplier_id: params.supplier_id || undefined,
      date_from: params.date_from || undefined,
      date_to: params.date_to || undefined,
    },
  });
  return data;
}

export async function createSupplierReturn(
  payload: SupplierReturnCreatePayload,
): Promise<SupplierReturnCreated> {
  const { data } = await api.post<SupplierReturnCreated>("/suppliers/returns", payload);
  return data;
}
