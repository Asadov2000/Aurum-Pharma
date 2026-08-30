// Mirrors backend Pydantic schemas in app/domains/catalog/schemas.py.

export type DispensingType = "prescription" | "otc" | "special";
export type StorageType = "normal" | "cold" | "frozen";
export type BarcodeType = "ean13" | "ean8" | "gs1_128" | "code128" | "qr" | "other";

export interface CatalogItem {
  id: string;
  tenant_id: string;
  brand_name: string;
  inn: string | null;
  manufacturer: string | null;
  form: string | null;
  dosage: string | null;
  pack_size: string | null;
  atx_code: string | null;
  dispensing_type: DispensingType;
  storage_type: StorageType;
  category: string | null;
  base_price: string | null; // Decimal arrives as string from FastAPI
  currency: string;
  image_version?: string | null;
  is_active: boolean;
  deleted_at?: string | null;
  created_at: string;
  updated_at: string;
  // Additive: available stock at the searched branch (POS passes branch_id).
  // Null/absent when searched without a branch.
  stock_available?: string | null;
}

export interface Barcode {
  id: string;
  code: string;
  code_type: BarcodeType;
}

export interface CatalogItemWithBarcodes extends CatalogItem {
  barcodes: Barcode[];
}

export interface CatalogList {
  items: CatalogItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface CatalogPickerList {
  items: CatalogItem[];
}

export interface CatalogSummary {
  total: number;
  active: number;
  inactive: number;
  archived: number;
  without_barcode: number;
  without_image: number;
}

export interface CatalogItemCreatePayload {
  brand_name: string;
  inn?: string | null;
  manufacturer?: string | null;
  form?: string | null;
  dosage?: string | null;
  pack_size?: string | null;
  atx_code?: string | null;
  dispensing_type?: DispensingType;
  storage_type?: StorageType;
  category?: string | null;
  base_price?: string | null;
}

export interface CatalogItemUpdatePayload {
  brand_name?: string;
  inn?: string | null;
  manufacturer?: string | null;
  form?: string | null;
  dosage?: string | null;
  pack_size?: string | null;
  atx_code?: string | null;
  dispensing_type?: DispensingType;
  storage_type?: StorageType;
  category?: string | null;
  base_price?: string | null;
  is_active?: boolean;
}

export interface BarcodeCreatePayload {
  code: string;
  code_type?: BarcodeType;
}

export interface CatalogSearchParams {
  q?: string;
  manufacturer?: string;
  category?: string;
  dispensing_type?: DispensingType;
  storage_type?: StorageType;
  lifecycle?: "active" | "inactive" | "archived" | "current" | "all";
  image_state?: "any" | "with_image" | "without_image";
  barcode_state?: "any" | "with_barcode" | "without_barcode";
  page?: number;
  page_size?: number;
  /** When set (POS register's branch), results carry stock_available. */
  branch_id?: string;
}

// ---- import ----

// Mirrors the backend's CatalogImportJob.status values exactly.
export type ImportStatus =
  | "pending"
  | "validating"
  | "importing"
  | "success"
  | "failed"
  | "rolled_back";

export type DuplicateStrategy = "skip" | "update" | "create_copy";

export interface ImportJob {
  id: string;
  tenant_id: string;
  source_filename: string;
  status: ImportStatus;
  duplicate_strategy: DuplicateStrategy;
  total_rows: number | null;
  valid_rows: number | null;
  error_rows: number | null;
  preview_data: Array<Record<string, unknown>> | null;
  errors: Array<Record<string, unknown>> | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  expires_at_for_rollback: string | null;
  rolled_back_at: string | null;
}
