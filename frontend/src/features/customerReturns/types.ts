export type CustomerReturnStatus = "pending" | "resolved";
export type CustomerReturnDispositionType = "disposed" | "supplier_claim" | "regulatory_transfer";
export type CustomerReturnReasonCode =
  | "damaged"
  | "quality_issue"
  | "wrong_item"
  | "expired"
  | "other";

export interface CustomerReturnItem {
  id: string;
  branch_id: string;
  branch_name: string;
  return_sale_id: string;
  return_receipt_number: string | null;
  parent_sale_id: string;
  parent_receipt_number: string | null;
  catalog_id: string;
  catalog_name: string;
  catalog_form: string | null;
  catalog_dosage: string | null;
  batch_id: string;
  batch_number: string | null;
  expires_at: string;
  qty: string;
  refund_reason: string | null;
  refund_comment: string | null;
  received_at: string;
  status: CustomerReturnStatus;
  disposition_type: CustomerReturnDispositionType | null;
  disposition_reason: CustomerReturnReasonCode | null;
  disposition_comment: string | null;
  resolved_at: string | null;
}

export interface CustomerReturnList {
  items: CustomerReturnItem[];
  total: number;
  pending: number;
  resolved: number;
  page: number;
  page_size: number;
}

export interface CustomerReturnSearchParams {
  status?: CustomerReturnStatus;
  branch_id?: string;
  search?: string;
  page?: number;
  page_size?: number;
}

export interface ResolveCustomerReturnPayload {
  operation_id: string;
  disposition_type: CustomerReturnDispositionType;
  reason_code: CustomerReturnReasonCode;
  comment: string | null;
}

export interface PhysicalCustomerReturnItem {
  operation_id: string;
  line_id: string;
  lot_id: string;
  branch_id: string;
  branch_name: string;
  report_timezone: string;
  parent_sale_id: string;
  parent_receipt_number: string;
  return_sale_id: string | null;
  return_receipt_number: string | null;
  catalog_id: string;
  catalog_name: string;
  base_units: number;
  unit_label: string;
  batch_number: string | null;
  expires_at: string;
  received_at: string;
  refund_reason: string | null;
  refund_comment: string | null;
  intake_kind: "cash_refund" | "zero_return";
  status: "pending";
}

export interface PhysicalCustomerReturnList {
  items: PhysicalCustomerReturnItem[];
  total: number;
  page: number;
  page_size: number;
}

export type PhysicalCustomerReturnSearchParams = Omit<CustomerReturnSearchParams, "status">;

export type MixedCustomerReturnItem = Omit<
  PhysicalCustomerReturnItem,
  "lot_id" | "base_units" | "unit_label"
> &
  (
    | {
        inventory_kind: "legacy_pack";
        batch_id: string;
        lot_id: null;
        legacy_quantity: string;
        base_units: null;
        unit_label: "уп.";
      }
    | {
        inventory_kind: "physical_unit";
        batch_id: null;
        lot_id: string;
        legacy_quantity: null;
        base_units: number;
        unit_label: string;
      }
  );
export interface MixedCustomerReturnList {
  items: MixedCustomerReturnItem[];
  total: number;
  page: number;
  page_size: number;
}
export type MixedCustomerReturnSearchParams = PhysicalCustomerReturnSearchParams;
