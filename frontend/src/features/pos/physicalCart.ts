import { api } from "@/lib/api";

import { type PhysicalPackaging, type PhysicalSaleFormat } from "./physicalCartOptions";

export interface PhysicalCartItem {
  id: string;
  tenant_id: string;
  branch_id: string;
  sale_id: string;
  catalog_id: string;
  position: number;
  sale_format: PhysicalSaleFormat;
  quantity: number;
  discount_amount: string;
  physical_packaging?: PhysicalPackaging | null;
}

export interface PhysicalCartItemPut {
  catalog_id: string;
  position: number;
  sale_format: PhysicalSaleFormat;
  quantity: number;
  discount_amount: string;
}

export interface PhysicalCartQuoteLine {
  inventory_kind: "legacy_pack" | "prepared_unit" | "physical_unit";
  id: string;
  catalog_id: string;
  position: number;
  quantity: number;
  original_retail_unit_price: string;
  gross_amount: string;
  discount_amount: string;
  net_amount: string;
}

export interface PhysicalCartQuote {
  quote: {
    schema_version: 2;
    state: "draft_quote";
    tenant_id: string;
    branch_id: string;
    sale_id: string;
    register_id: string;
    shift_id: string;
    cashier_id: string;
    lines: PhysicalCartQuoteLine[];
    total_amount: string;
    currency: "TJS";
  };
  revision_hash: string;
}

function cartPath(saleId: string): string {
  return `/sales/${encodeURIComponent(saleId)}/physical-cart`;
}

export async function putPhysicalCartItem(
  saleId: string,
  itemId: string,
  payload: PhysicalCartItemPut,
): Promise<PhysicalCartItem> {
  const { data } = await api.put<PhysicalCartItem>(
    `${cartPath(saleId)}/items/${encodeURIComponent(itemId)}`,
    payload,
    { timeout: 8_000 },
  );
  return data;
}

export async function deletePhysicalCartItem(saleId: string, itemId: string): Promise<void> {
  await api.delete(`${cartPath(saleId)}/items/${encodeURIComponent(itemId)}`, {
    timeout: 8_000,
  });
}

export async function getPhysicalCartQuote(
  saleId: string,
  signal?: AbortSignal,
): Promise<PhysicalCartQuote> {
  const { data } = await api.get<PhysicalCartQuote>(`${cartPath(saleId)}/quote`, {
    signal,
    timeout: 8_000,
  });
  return data;
}
