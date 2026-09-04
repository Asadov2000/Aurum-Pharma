import { api } from "@/lib/api";

import {
  type BatchDetails,
  type BatchList,
  type BatchSearchParams,
  type Movement,
  type WriteOff,
  type WriteOffCreatePayload,
} from "./types";

export async function listBatches(
  params: BatchSearchParams,
  signal?: AbortSignal,
): Promise<BatchList> {
  const { data } = await api.get<BatchList>("/batches", {
    signal,
    params: {
      catalog_id: params.catalog_id || undefined,
      branch_id: params.branch_id || undefined,
      expiry_status: params.expiry_status || undefined,
      batch_number: params.batch_number || undefined,
      is_blocked: params.is_blocked,
      show_empty: params.show_empty ?? false,
      page: params.page ?? 1,
      page_size: params.page_size ?? 50,
    },
  });
  return data;
}

export async function getBatch(id: string, signal?: AbortSignal): Promise<BatchDetails> {
  const { data } = await api.get<BatchDetails>(`/batches/${id}`, { signal });
  return data;
}

export async function listMovements(
  batchId: string,
  limit = 50,
  signal?: AbortSignal,
): Promise<Movement[]> {
  const { data } = await api.get<Movement[]>(`/batches/${batchId}/movements`, {
    params: { limit },
    signal,
  });
  return data;
}

export async function writeOff(batchId: string, payload: WriteOffCreatePayload): Promise<WriteOff> {
  const { data } = await api.post<WriteOff>(`/batches/${batchId}/write-off`, payload);
  return data;
}
