// Mirrors backend Pydantic schemas in app/domains/incoming/schemas.py.

// Backend uses these literal values: draft / accepted / rejected.
export type IncomingStatus = "draft" | "accepted" | "rejected";
export type IncomingStockMode = "legacy_pack" | "prepared_unit_v1" | "physical_unit_v2";
export interface IncomingAcceptPayload {
  stock_mode: "legacy_pack" | "physical_unit_v2";
  expected_revision?: string;
}

export interface IncomingDocument {
  id: string;
  tenant_id: string;
  branch_id: string;
  supplier_id: string;
  document_number: string | null;
  document_date: string; // ISO date
  status: IncomingStatus;
  total_amount: string | null; // Redacted without cost access
  currency: string;
  notes: string | null;
  document_file_path: string | null;
  created_at: string;
  updated_at: string;
  accepted_at: string | null;
  stock_mode?: IncomingStockMode | null;
  branch_name?: string | null;
  supplier_name?: string | null;
}

export interface IncomingUnitPackaging {
  version: 1;
  unit: "tablet" | "capsule" | "ampoule" | "sachet" | "suppository";
  units_per_pack: number;
  dispense_step: number;
}

export interface IncomingItem {
  id: string;
  document_id: string;
  catalog_id: string;
  batch_number: string | null;
  manufactured_at: string | null;
  expires_at: string;
  qty: string;
  purchase_price: string | null;
  sale_price: string;
  currency: string;
  created_batch_id: string | null;
  catalog_name?: string | null;
  catalog_form?: string | null;
  catalog_dosage?: string | null;
  catalog_pack_size?: string | null;
  unit_packaging_preparation?: IncomingUnitPackaging | null;
}

export interface IncomingDocumentWithItems extends IncomingDocument {
  items: IncomingItem[];
  acceptance_revision?: string | null;
  physical_receiving_ready?: boolean;
}

export interface IncomingDraftFields {
  source_name: string;
  catalog_id: string;
  batch_number: string;
  manufactured_at: string;
  expires_at: string;
  qty: string;
  purchase_price: string;
  sale_price: string;
}

export type InvoiceColumn =
  | "source_name"
  | "batch_number"
  | "manufactured_at"
  | "expires_at"
  | "qty"
  | "purchase_price"
  | "sale_price";

export type InvoiceColumnMapping = Record<InvoiceColumn, number | null>;

export interface InvoicePreview {
  headers: string[];
  rows: string[][];
  suggested_mapping: InvoiceColumnMapping;
  total_rows: number;
  warnings: string[];
}

export interface InvoiceImportResult {
  imported_count: number;
  existing_count: number;
}

export interface IncomingDraftRow {
  id: string;
  document_id: string;
  revision: number;
  fields: Omit<IncomingDraftFields, "purchase_price"> & { purchase_price: string | null };
  catalog_name: string | null;
  created_at: string;
  updated_at: string;
}

export interface IncomingDraftSavePayload {
  expected_revision: number;
  fields: IncomingDraftFields;
}

export interface IncomingDocumentList {
  items: IncomingDocument[];
  total: number;
  page: number;
  page_size: number;
  summary: IncomingDocumentSummary;
}

export interface IncomingDocumentSummary {
  all_count: number;
  draft_count: number;
  accepted_count: number;
  rejected_count: number;
  accepted_amount: string | null;
  currency: string;
}

export interface IncomingDocumentCreatePayload {
  operation_id: string;
  branch_id: string;
  supplier_id: string;
  document_date: string;
  document_number?: string | null;
  notes?: string | null;
}

export interface IncomingDocumentUpdatePayload {
  branch_id?: string;
  supplier_id?: string;
  document_date?: string;
  document_number?: string | null;
  notes?: string | null;
  document_file_path?: string | null;
}

export interface IncomingItemCreatePayload {
  operation_id: string;
  catalog_id: string;
  batch_number?: string | null;
  manufactured_at?: string | null;
  expires_at: string;
  qty: string;
  purchase_price: string;
  sale_price: string;
}

export interface IncomingItemUpdatePayload {
  prepare_unit_sale?: boolean;
  catalog_id?: string;
  batch_number?: string | null;
  manufactured_at?: string | null;
  expires_at?: string;
  qty?: string;
  purchase_price?: string;
  sale_price?: string;
}

export interface IncomingSearchParams {
  branch_id?: string;
  supplier_id?: string;
  status?: IncomingStatus;
  document_number?: string;
  date_from?: string;
  date_to?: string;
  page?: number;
  page_size?: number;
}
