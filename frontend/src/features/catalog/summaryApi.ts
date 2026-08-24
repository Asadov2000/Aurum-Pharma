import { api } from "@/lib/api";

import { type CatalogSummary } from "./types";

export async function getCatalogSummary(): Promise<CatalogSummary> {
  const { data } = await api.get<CatalogSummary>("/catalog/summary");
  return data;
}
