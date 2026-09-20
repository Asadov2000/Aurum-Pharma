import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { getEdgeCatalogItem, listEdgeCatalog, listEdgeInventory, listEdgeSuppliers } from "./api";
import { type EdgePageParams } from "./types";

export const edgeReadModelKeys = {
  root: ["edge-read-model"] as const,
  catalog: (params: EdgePageParams) => [...edgeReadModelKeys.root, "catalog", params] as const,
  catalogItem: (branchId: string, itemId: string) =>
    [...edgeReadModelKeys.root, "catalog-item", branchId, itemId] as const,
  suppliers: (params: EdgePageParams) => [...edgeReadModelKeys.root, "suppliers", params] as const,
  inventory: (params: EdgePageParams & { readonly showEmpty?: boolean }) =>
    [...edgeReadModelKeys.root, "inventory", params] as const,
};

export function useEdgeCatalogQuery(params: EdgePageParams) {
  return useQuery({
    queryKey: edgeReadModelKeys.catalog(params),
    queryFn: ({ signal }) => listEdgeCatalog(params, signal),
    placeholderData: keepPreviousData,
  });
}

export function useEdgeCatalogItemQuery(branchId: string, itemId: string | null) {
  return useQuery({
    queryKey: edgeReadModelKeys.catalogItem(branchId, itemId ?? ""),
    queryFn: ({ signal }) => getEdgeCatalogItem(itemId as string, branchId, signal),
    enabled: itemId !== null,
  });
}

export function useEdgeSuppliersQuery(params: EdgePageParams) {
  return useQuery({
    queryKey: edgeReadModelKeys.suppliers(params),
    queryFn: ({ signal }) => listEdgeSuppliers(params, signal),
    placeholderData: keepPreviousData,
  });
}

export function useEdgeInventoryQuery(params: EdgePageParams & { readonly showEmpty?: boolean }) {
  return useQuery({
    queryKey: edgeReadModelKeys.inventory(params),
    queryFn: ({ signal }) => listEdgeInventory(params, signal),
    placeholderData: keepPreviousData,
  });
}
