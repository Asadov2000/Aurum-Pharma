import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { listCatalogForPicker, type CatalogPickerSearchParams } from "./pickerApi";

export function useCatalogPickerQuery(params: CatalogPickerSearchParams, enabled: boolean) {
  return useQuery({
    queryKey: ["catalog", "list", params],
    queryFn: () => listCatalogForPicker(params),
    placeholderData: keepPreviousData,
    enabled,
  });
}
