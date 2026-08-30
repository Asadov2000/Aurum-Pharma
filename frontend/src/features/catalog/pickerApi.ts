import { api } from "@/lib/api";

import { type CatalogPickerList } from "./types";

export interface CatalogPickerSearchParams {
  q: string;
  page: number;
  page_size: number;
  branch_id?: string;
}

export async function listCatalogForPicker(
  params: CatalogPickerSearchParams,
  signal?: AbortSignal,
): Promise<CatalogPickerList> {
  const { data } = await api.get<CatalogPickerList>("/catalog/picker", {
    params: {
      q: params.q,
      branch_id: params.branch_id,
      limit: params.page_size,
    },
    signal,
  });
  return data;
}
