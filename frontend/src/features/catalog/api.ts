import { api } from "@/lib/api";

import {
  type Barcode,
  type BarcodeCreatePayload,
  type CatalogItem,
  type CatalogItemCreatePayload,
  type CatalogItemUpdatePayload,
  type CatalogItemWithBarcodes,
  type CatalogList,
  type CatalogSearchParams,
  type DuplicateStrategy,
  type ImportJob,
} from "./types";

export async function listCatalog(params: CatalogSearchParams): Promise<CatalogList> {
  const { data } = await api.get<CatalogList>("/catalog", {
    params: {
      q: params.q || undefined,
      manufacturer: params.manufacturer || undefined,
      category: params.category || undefined,
      dispensing_type: params.dispensing_type || undefined,
      storage_type: params.storage_type || undefined,
      lifecycle: params.lifecycle || undefined,
      image_state: params.image_state || undefined,
      barcode_state: params.barcode_state || undefined,
      page: params.page ?? 1,
      page_size: params.page_size ?? 50,
      branch_id: params.branch_id || undefined,
    },
  });
  return data;
}

export async function getCatalogItem(id: string): Promise<CatalogItemWithBarcodes> {
  const { data } = await api.get<CatalogItemWithBarcodes>(`/catalog/${id}`);
  return data;
}

export async function createCatalogItem(payload: CatalogItemCreatePayload): Promise<CatalogItem> {
  const { data } = await api.post<CatalogItem>("/catalog", payload);
  return data;
}

export async function updateCatalogItem(
  id: string,
  payload: CatalogItemUpdatePayload,
): Promise<CatalogItem> {
  const { data } = await api.patch<CatalogItem>(`/catalog/${id}`, payload);
  return data;
}

export async function deleteCatalogItem(id: string): Promise<void> {
  await api.delete(`/catalog/${id}`);
}

export async function restoreCatalogItem(id: string): Promise<CatalogItem> {
  const { data } = await api.post<CatalogItem>(`/catalog/${id}/restore`);
  return data;
}

export async function findByBarcode(code: string): Promise<CatalogItem> {
  const { data } = await api.get<CatalogItem>(`/catalog/by-barcode/${encodeURIComponent(code)}`);
  return data;
}

// ---- barcodes ----

export async function addBarcode(itemId: string, payload: BarcodeCreatePayload): Promise<Barcode> {
  const { data } = await api.post<Barcode>(`/catalog/${itemId}/barcodes`, payload);
  return data;
}

export async function deleteBarcode(itemId: string, barcodeId: string): Promise<void> {
  await api.delete(`/catalog/${itemId}/barcodes/${barcodeId}`);
}

// ---- import ----

export async function uploadImport(file: File): Promise<ImportJob> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await api.post<ImportJob>("/catalog/import/upload", form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}

export async function previewImport(jobId: string): Promise<ImportJob> {
  const { data } = await api.post<ImportJob>(`/catalog/import/${jobId}/preview`);
  return data;
}

export async function confirmImport(
  jobId: string,
  duplicate_strategy: DuplicateStrategy,
): Promise<ImportJob> {
  const { data } = await api.post<ImportJob>(`/catalog/import/${jobId}/confirm`, {
    duplicate_strategy,
  });
  return data;
}

export async function getImportJob(jobId: string): Promise<ImportJob> {
  const { data } = await api.get<ImportJob>(`/catalog/import/${jobId}`);
  return data;
}

export async function rollbackImport(jobId: string): Promise<ImportJob> {
  const { data } = await api.post<ImportJob>(`/catalog/import/${jobId}/rollback`);
  return data;
}
