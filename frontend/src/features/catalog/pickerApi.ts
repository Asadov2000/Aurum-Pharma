import { api } from "@/lib/api";

import { type CatalogList } from "./types";

export interface CatalogPickerSearchParams {
  q: string;
  page: number;
  page_size: number;
  branch_id?: string;
}

export async function listCatalogForPicker(
  params: CatalogPickerSearchParams,
): Promise<CatalogList> {
  const { data } = await api.get<CatalogList>("/catalog", { params });
  return data;
}
