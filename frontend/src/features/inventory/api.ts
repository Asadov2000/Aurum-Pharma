import { api } from "@/lib/api";

import {
  type BatchDetails,
  type BatchList,
  type BatchSearchParams,
  type Movement,
  type WriteOff,
  type WriteOffCreatePayload,
} from "./types";

export async function listBatches(params: BatchSearchParams): Promise<BatchList> {
  const { data } = await api.get<BatchList>("/batches", {
    params: {
      catalog_id: params.catalog_id || undefined,
      branch_id: params.branch_id || undefined,
      expiry_status: params.expiry_status || undefined,
      show_empty: params.show_empty ?? false,
      page: params.page ?? 1,
      page_size: params.page_size ?? 50,
    },
  });
  return data;
}

export async function getBatch(id: string): Promise<BatchDetails> {
  const { data } = await api.get<BatchDetails>(`/batches/${id}`);
  return data;
}

export async function listMovements(batchId: string): Promise<Movement[]> {
  const { data } = await api.get<Movement[]>(`/batches/${batchId}/movements`);
  return data;
}

export async function writeOff(
  batchId: string,
  payload: WriteOffCreatePayload,
): Promise<WriteOff> {
  const { data } = await api.post<WriteOff>(`/batches/${batchId}/write-off`, payload);
  return data;
}
