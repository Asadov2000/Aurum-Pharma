import { api } from "@/lib/api";

import { recordEdgeSnapshotMetadata } from "./snapshotStore";
import {
  type EdgeCatalogDetailsResponse,
  type EdgeCatalogResponse,
  type EdgeInventoryResponse,
  type EdgePageParams,
  type EdgeSuppliersResponse,
} from "./types";

export async function listEdgeCatalog(
  params: EdgePageParams,
  signal?: AbortSignal,
): Promise<EdgeCatalogResponse> {
  const { data } = await api.get<EdgeCatalogResponse>("/edge/read-model/catalog", {
    signal,
    params: pageParams(params),
  });
  recordEdgeSnapshotMetadata(data.metadata);
  return data;
}

export async function getEdgeCatalogItem(
  itemId: string,
  branchId: string,
  signal?: AbortSignal,
): Promise<EdgeCatalogDetailsResponse> {
  const { data } = await api.get<EdgeCatalogDetailsResponse>(`/edge/read-model/catalog/${itemId}`, {
    signal,
    params: { branch_id: branchId },
  });
  recordEdgeSnapshotMetadata(data.metadata);
  return data;
}

export async function listEdgeSuppliers(
  params: EdgePageParams,
  signal?: AbortSignal,
): Promise<EdgeSuppliersResponse> {
  const { data } = await api.get<EdgeSuppliersResponse>("/edge/read-model/suppliers", {
    signal,
    params: pageParams(params),
  });
  recordEdgeSnapshotMetadata(data.metadata);
  return data;
}

export async function listEdgeInventory(
  params: EdgePageParams & { readonly showEmpty?: boolean },
  signal?: AbortSignal,
): Promise<EdgeInventoryResponse> {
  const { data } = await api.get<EdgeInventoryResponse>("/edge/read-model/inventory", {
    signal,
    params: {
      branch_id: params.branchId,
      page: params.page ?? 1,
      page_size: params.pageSize ?? 50,
      show_empty: params.showEmpty ?? false,
    },
  });
  recordEdgeSnapshotMetadata(data.metadata);
  return data;
}

function pageParams(params: EdgePageParams): Record<string, string | number | undefined> {
  return {
    branch_id: params.branchId,
    page: params.page ?? 1,
    page_size: params.pageSize ?? 50,
    q: params.query || undefined,
  };
}
