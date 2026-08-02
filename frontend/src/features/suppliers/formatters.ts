export function formatSupplierMoney(value: string | number, currency = "TJS"): string {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return `0,00 ${currency}`;
  return `${amount.toLocaleString("ru-RU", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })} ${currency}`;
}

export function formatSupplierQuantity(value: string | number): string {
  const quantity = Number(value);
  if (!Number.isFinite(quantity)) return "0";
  return quantity.toLocaleString("ru-RU", { maximumFractionDigits: 3 });
}

export function formatSupplierDate(value: string): string {
  const date = new Date(`${value}T00:00:00`);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString("ru-RU");
}

export function formatSupplierDateTime(value: string, timezone: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  try {
    return new Intl.DateTimeFormat("ru-RU", {
      dateStyle: "short",
      timeStyle: "short",
      timeZone: timezone,
    }).format(date);
  } catch {
    return date.toLocaleString("ru-RU");
  }
}

export function supplierProductSubtitle(product: {
  catalog_form: string | null;
  catalog_dosage: string | null;
  catalog_pack_size: string | null;
}): string {
  return [product.catalog_form, product.catalog_dosage, product.catalog_pack_size]
    .filter(Boolean)
    .join(" · ");
}
