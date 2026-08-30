import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { listCatalogForPicker, type CatalogPickerSearchParams } from "./pickerApi";

export function useCatalogPickerQuery(params: CatalogPickerSearchParams, enabled: boolean) {
  return useQuery({
    queryKey: ["catalog", "picker", params],
    queryFn: ({ signal }) => listCatalogForPicker(params, signal),
    placeholderData: keepPreviousData,
    enabled,
    staleTime: 30_000,
    gcTime: 5 * 60_000,
  });
}
