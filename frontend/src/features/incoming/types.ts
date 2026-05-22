// Mirrors backend Pydantic schemas in app/domains/incoming/schemas.py.

// Backend uses these literal values: draft / accepted / rejected.
export type IncomingStatus = "draft" | "accepted" | "rejected";

export interface IncomingDocument {
  id: string;
  tenant_id: string;
  branch_id: string;
  supplier_id: string;
  document_number: string | null;
  document_date: string; // ISO date
  status: IncomingStatus;
  total_amount: string; // Decimal
  currency: string;
  notes: string | null;
  document_file_path: string | null;
  created_at: string;
  updated_at: string;
  accepted_at: string | null;
}

export interface IncomingItem {
  id: string;
  document_id: string;
  catalog_id: string;
  batch_number: string | null;
  manufactured_at: string | null;
  expires_at: string;
  qty: string;
  purchase_price: string;
  sale_price: string;
  currency: string;
  created_batch_id: string | null;
}

export interface IncomingDocumentWithItems extends IncomingDocument {
  items: IncomingItem[];
}

export interface IncomingDocumentCreatePayload {
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
  catalog_id: string;
  batch_number?: string | null;
  manufactured_at?: string | null;
  expires_at: string;
  qty: string;
  purchase_price: string;
  sale_price: string;
}

export interface IncomingItemUpdatePayload {
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
  date_from?: string;
  date_to?: string;
}
