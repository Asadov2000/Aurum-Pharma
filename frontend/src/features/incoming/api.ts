import { api } from "@/lib/api";

import {
  type IncomingDocument,
  type IncomingDraftRow,
  type IncomingDraftSavePayload,
  type IncomingDocumentCreatePayload,
  type IncomingDocumentList,
  type IncomingDocumentUpdatePayload,
  type IncomingDocumentWithItems,
  type IncomingItem,
  type IncomingItemCreatePayload,
  type IncomingItemUpdatePayload,
  type IncomingSearchParams,
  type InvoiceColumnMapping,
  type InvoiceImportResult,
  type InvoicePreview,
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

export async function listIncomingDraftRows(
  documentId: string,
  signal?: AbortSignal,
): Promise<IncomingDraftRow[]> {
  const { data } = await api.get<IncomingDraftRow[]>(`/incoming/${documentId}/draft-rows`, {
    signal,
  });
  return data;
}

export async function previewIncomingInvoice(
  documentId: string,
  file: File,
): Promise<InvoicePreview> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await api.post<InvoicePreview>(`/incoming/${documentId}/invoice/preview`, form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}

export async function importIncomingInvoice(
  documentId: string,
  file: File,
  mapping: InvoiceColumnMapping,
  operationId: string,
): Promise<InvoiceImportResult> {
  const form = new FormData();
  form.append("file", file);
  form.append("mapping", JSON.stringify(mapping));
  form.append("operation_id", operationId);
  const { data } = await api.post<InvoiceImportResult>(
    `/incoming/${documentId}/invoice/import`,
    form,
    { headers: { "Content-Type": "multipart/form-data" } },
  );
  return data;
}

export async function saveIncomingDraftRow(
  documentId: string,
  rowId: string,
  payload: IncomingDraftSavePayload,
): Promise<IncomingDraftRow> {
  const { data } = await api.put<IncomingDraftRow>(
    `/incoming/${documentId}/draft-rows/${rowId}`,
    payload,
  );
  return data;
}

export async function commitIncomingDraftRow(
  documentId: string,
  rowId: string,
  revision: number,
): Promise<IncomingItem> {
  const { data } = await api.post<IncomingItem>(
    `/incoming/${documentId}/draft-rows/${rowId}/commit`,
    { expected_revision: revision },
  );
  return data;
}

export async function deleteIncomingDraftRow(
  documentId: string,
  rowId: string,
  revision: number,
): Promise<void> {
  await api.delete(`/incoming/${documentId}/draft-rows/${rowId}`, {
    params: { expected_revision: revision },
  });
}
