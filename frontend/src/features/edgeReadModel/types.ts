import { type CatalogItem, type CatalogItemWithBarcodes } from "@/features/catalog/types";
import { type BatchList } from "@/features/inventory/types";
import { type Supplier } from "@/features/suppliers/types";

export interface EdgeSnapshotMetadata {
  readonly snapshot_id: string;
  readonly source_revision: number;
  readonly source_generated_at: string;
  readonly last_confirmed_at: string;
  readonly age_seconds: number;
  readonly is_stale: boolean;
  readonly source: "local_edge";
}

export interface EdgeCatalogResponse {
  readonly metadata: EdgeSnapshotMetadata;
  readonly items: CatalogItem[];
  readonly total: number;
  readonly page: number;
  readonly page_size: number;
}

export interface EdgeCatalogDetailsResponse {
  readonly metadata: EdgeSnapshotMetadata;
  readonly item: CatalogItemWithBarcodes;
}

export interface EdgeSuppliersResponse {
  readonly metadata: EdgeSnapshotMetadata;
  readonly items: Supplier[];
  readonly total: number;
  readonly page: number;
  readonly page_size: number;
}

export interface EdgeInventoryResponse extends BatchList {
  readonly metadata: EdgeSnapshotMetadata;
}

export interface EdgePageParams {
  readonly branchId: string;
  readonly page?: number;
  readonly pageSize?: number;
  readonly query?: string;
}
