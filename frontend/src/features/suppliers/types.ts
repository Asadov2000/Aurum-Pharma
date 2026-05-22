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

export interface SupplierCreatePayload {
  name: string;
  legal_name?: string | null;
  inn_or_tin?: string | null;
  contact_person?: string | null;
  phone?: string | null;
  email?: string | null;
  address?: string | null;
  notes?: string | null;
}

export interface SupplierUpdatePayload extends SupplierCreatePayload {
  is_active?: boolean;
}

export interface SupplierReturn {
  id: string;
  supplier_id: string;
  batch_id: string;
  source_document_id: string | null;
  qty: string;
  amount: string;
  currency: string;
  reason: string;
  comment: string | null;
  created_at: string;
}

export interface SupplierReturnCreatePayload {
  supplier_id: string;
  batch_id: string;
  qty: string;
  reason: string;
  comment?: string | null;
  source_document_id?: string | null;
}

export interface SupplierReturnCreated extends SupplierReturn {
  warning: string | null;
}
