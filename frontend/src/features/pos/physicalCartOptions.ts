import { type UnitSalePreparation } from "@/features/catalog/types";
import { api } from "@/lib/api";

export type PhysicalSaleFormat = "pack" | "blister" | "unit";

export interface PhysicalPackaging {
  version: 2;
  unit: UnitSalePreparation["unit"];
  units_per_pack: number;
  units_per_blister: number | null;
  dispense_step: number;
}

export interface PhysicalSaleOption {
  sale_format: PhysicalSaleFormat;
  configured: boolean;
  quantity_step: number | null;
  // Exact decimal strings; the cashier client must not derive prices by division.
  retail_unit_price: string | null;
  unavailable_reason:
    | "not_prepared"
    | "catalog_unavailable"
    | "unknown_blister"
    | "price_unavailable"
    | null;
}

/** A shared physical snapshot, not stock reserved for this cart or checkout authority. */
export interface PhysicalCartOptions {
  state: "physical_options_preview";
  tenant_id: string;
  branch_id: string;
  sale_id: string;
  catalog_id: string;
  stock_date: string;
  packaging: PhysicalPackaging | null;
  currency: "TJS";
  checkout_enabled: false;
  stock_reserved: false;
  stock: {
    base_units: number;
    closed_packs: number;
    intact_blisters: number | null;
  };
  options: PhysicalSaleOption[];
}

export async function getPhysicalCartOptions(
  saleId: string,
  catalogId: string,
  signal?: AbortSignal,
): Promise<PhysicalCartOptions> {
  const { data } = await api.get<PhysicalCartOptions>(
    `/sales/${encodeURIComponent(saleId)}/physical-cart/options/${encodeURIComponent(catalogId)}`,
    { signal, timeout: 8_000 },
  );
  return data;
}
