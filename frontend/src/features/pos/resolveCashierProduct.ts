import { type CatalogItem } from "@/features/catalog/types";
import { getPhysicalCartOptions } from "./physicalCartOptions";

/** Never turn an options/network error into permission to use the legacy ledger. */
export async function resolveCashierProduct(
  saleId: string,
  branchId: string,
  item: CatalogItem,
  signal: AbortSignal,
) {
  if (!item.is_active || item.deleted_at) throw new Error("Товар недоступен для продажи");
  const options = await getPhysicalCartOptions(saleId, item.id, signal);
  if (
    options.sale_id !== saleId ||
    options.branch_id !== branchId ||
    options.catalog_id !== item.id ||
    options.tenant_id !== item.tenant_id
  ) {
    throw new Error("Не удалось подтвердить параметры выбранного товара");
  }
  if (options.packaging !== null) return { kind: "physical" as const, options };
  if (
    options.options.length === 3 &&
    new Set(options.options.map((option) => option.sale_format)).size === 3 &&
    options.options.every(
      (option) => !option.configured && option.unavailable_reason === "not_prepared",
    )
  ) {
    return { kind: "ordinary" as const };
  }
  throw new Error("Товар недоступен для продажи");
}
