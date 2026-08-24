import { useQuery } from "@tanstack/react-query";

import { getCatalogSummary } from "./summaryApi";

export function useCatalogSummaryQuery() {
  return useQuery({
    queryKey: ["catalog", "summary"],
    queryFn: getCatalogSummary,
    staleTime: 30_000,
  });
}
