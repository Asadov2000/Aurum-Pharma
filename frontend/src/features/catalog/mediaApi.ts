import { api } from "@/lib/api";

import { type CatalogItem } from "./types";

export async function getCatalogImage(
  itemId: string,
  version: string,
  variant: "display" | "thumbnail",
): Promise<Blob> {
  const { data } = await api.get<Blob>(`/catalog/${itemId}/image/${version}/${variant}`, {
    responseType: "blob",
  });
  return data;
}

export async function uploadCatalogImage(itemId: string, file: File): Promise<CatalogItem> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await api.put<CatalogItem>(`/catalog/${itemId}/image`, form, {
    // The shared client defaults to JSON. Removing that default lets the
    // browser add the multipart boundary required by FastAPI.
    headers: { "Content-Type": undefined },
  });
  return data;
}

export async function deleteCatalogImage(itemId: string): Promise<CatalogItem> {
  const { data } = await api.delete<CatalogItem>(`/catalog/${itemId}/image`);
  return data;
}
