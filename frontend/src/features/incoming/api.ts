import { api } from "@/lib/api";

import {
  type IncomingDocument,
  type IncomingDocumentCreatePayload,
  type IncomingDocumentList,
  type IncomingDocumentUpdatePayload,
  type IncomingDocumentWithItems,
  type IncomingItem,
  type IncomingItemCreatePayload,
  type IncomingItemUpdatePayload,
  type IncomingSearchParams,
} from "./types";

export async function listIncoming(
  params: IncomingSearchParams,
  signal?: AbortSignal,
): Promise<IncomingDocumentList> {
  const { data } = await api.get<IncomingDocumentList>("/incoming", {
    params: {
      branch_id: params.branch_id || undefined,
      supplier_id: params.supplier_id || undefined,
      status: params.status || undefined,
      document_number: params.document_number || undefined,
      date_from: params.date_from || undefined,
      date_to: params.date_to || undefined,
      page: params.page,
      page_size: params.page_size,
    },
    signal,
  });
  return data;
}

export async function createIncoming(
  payload: IncomingDocumentCreatePayload,
): Promise<IncomingDocument> {
  const { data } = await api.post<IncomingDocument>("/incoming", payload);
  return data;
}

export async function getIncoming(
  id: string,
  signal?: AbortSignal,
): Promise<IncomingDocumentWithItems> {
  const { data } = await api.get<IncomingDocumentWithItems>(`/incoming/${id}`, { signal });
  return data;
}

export async function updateIncoming(
  id: string,
  payload: IncomingDocumentUpdatePayload,
): Promise<IncomingDocument> {
  const { data } = await api.patch<IncomingDocument>(`/incoming/${id}`, payload);
  return data;
}

export async function addIncomingItem(
  documentId: string,
  payload: IncomingItemCreatePayload,
): Promise<IncomingItem> {
  const { data } = await api.post<IncomingItem>(`/incoming/${documentId}/items`, payload);
  return data;
}

export async function updateIncomingItem(
  documentId: string,
  itemId: string,
  payload: IncomingItemUpdatePayload,
): Promise<IncomingItem> {
  const { data } = await api.patch<IncomingItem>(
    `/incoming/${documentId}/items/${itemId}`,
    payload,
  );
  return data;
}

export async function deleteIncomingItem(documentId: string, itemId: string): Promise<void> {
  await api.delete(`/incoming/${documentId}/items/${itemId}`);
}

export async function acceptIncoming(id: string): Promise<IncomingDocument> {
  const { data } = await api.post<IncomingDocument>(`/incoming/${id}/accept`);
  return data;
}

export async function rejectIncoming(id: string): Promise<IncomingDocument> {
  const { data } = await api.post<IncomingDocument>(`/incoming/${id}/reject`);
  return data;
}
