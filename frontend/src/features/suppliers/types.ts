// Mirrors backend Pydantic schemas in app/domains/suppliers/schemas.py.

export interface Supplier {
  id: string;
  tenant_id: string;
  name: string;
  legal_name: string | null;
  inn_or_tin: string | null;
  contact_person: string | null;
  phone: string | null;
  email: string | null;
  address: string | null;
  notes: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface SupplierSearchSummary {
  all_count: number;
  active_count: number;
  inactive_count: number;
  with_contact_count: number;
}

export interface SupplierListResponse {
  items: Supplier[];
  total: number;
  page: number;
  page_size: number;
  summary: SupplierSearchSummary;
}

export interface SupplierSearchParams {
  q?: string;
  is_active?: boolean;
  page?: number;
  page_size?: number;
}

export interface SupplierOption {
  id: string;
  name: string;
  is_active: boolean;
}

export interface SupplierOptionSearchParams {
  q?: string;
  include_inactive?: boolean;
  selected_id?: string;
  limit?: number;
}

export interface SupplierOptionList {
  items: SupplierOption[];
}

export interface SupplierCreatePayload {
  operation_id: string;
  name: string;
  legal_name?: string | null;
  inn_or_tin?: string | null;
  contact_person?: string | null;
  phone?: string | null;
  email?: string | null;
  address?: string | null;
  notes?: string | null;
}

export interface SupplierUpdatePayload extends Omit<SupplierCreatePayload, "operation_id"> {
  is_active?: boolean;
}

export type SupplierReturnReason =
  | "damaged"
  | "expired"
  | "incorrect_delivery"
  | "quality_issue"
  | "other";

export interface SupplierReturn {
  id: string;
  supplier_id: string;
  batch_id: string;
  source_document_id: string;
  qty: string;
  amount: string | null;
  currency: string;
  reason: SupplierReturnReason;
  comment: string | null;
  created_at: string;
}

export interface SupplierReturnDetails extends SupplierReturn {
  supplier_name: string;
  branch_id: string;
  branch_name: string;
  batch_number: string | null;
  catalog_name: string;
  catalog_form: string | null;
  catalog_dosage: string | null;
  catalog_pack_size: string | null;
  source_document_number: string | null;
  report_timezone: string;
}

export interface SupplierReturnCreatePayload {
  operation_id: string;
  supplier_id: string;
  batch_id: string;
  qty: string;
  reason: SupplierReturnReason;
  comment?: string | null;
  source_document_id?: string | null;
}

export interface SupplierReturnCreated extends SupplierReturn {
  warning: string | null;
}

export interface SupplierReturnSearchParams {
  supplier_id?: string;
  branch_id?: string;
  reason?: SupplierReturnReason;
  date_from?: string;
  date_to?: string;
  page?: number;
  page_size?: number;
}

export interface SupplierReturnList {
  items: SupplierReturnDetails[];
  total: number;
  page: number;
  page_size: number;
  summary: {
    total_qty: string;
    total_amount: string | null;
  };
}

export interface SupplierReturnCandidate {
  batch_id: string;
  source_document_id: string;
  document_number: string | null;
  document_date: string;
  branch_id: string;
  branch_name: string;
  catalog_name: string;
  catalog_form: string | null;
  catalog_dosage: string | null;
  catalog_pack_size: string | null;
  batch_number: string | null;
  expires_at: string;
  qty_remaining: string;
  purchase_price: string;
  currency: string;
}

export interface SupplierReturnCandidateSearchParams {
  supplier_id: string;
  branch_id?: string;
  q?: string;
  page?: number;
  page_size?: number;
}

export interface SupplierReturnCandidateList {
  items: SupplierReturnCandidate[];
  total: number;
  page: number;
  page_size: number;
}
