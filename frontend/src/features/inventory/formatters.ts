import { type BatchWithExpiry } from "./types";

const dateFormatter = new Intl.DateTimeFormat("ru-RU");
const moneyFormatter = new Intl.NumberFormat("ru-RU", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const quantityFormatter = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 });
const dateTimeFormatters = new Map<string, Intl.DateTimeFormat>();

export function formatInventoryDate(value: string): string {
  const [year, month, day] = value.split("-").map(Number);
  if (!year || !month || !day) return value;
  return dateFormatter.format(new Date(year, month - 1, day, 12));
}

export function formatInventoryMoney(value: string | number, currency = "TJS"): string {
  return `${moneyFormatter.format(Number(value))} ${currency}`;
}

export function formatInventoryQuantity(value: string | number): string {
  return quantityFormatter.format(Number(value));
}

export function formatInventoryDateTime(value: string, timeZone: string): string {
  let formatter = dateTimeFormatters.get(timeZone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat("ru-RU", {
      dateStyle: "short",
      timeStyle: "short",
      timeZone,
    });
    dateTimeFormatters.set(timeZone, formatter);
  }
  return formatter.format(new Date(value));
}

export function productSubtitle(batch: BatchWithExpiry): string | null {
  const parts = [batch.catalog_form, batch.catalog_dosage, batch.catalog_pack_size].filter(Boolean);
  return parts.length > 0 ? parts.join(" · ") : null;
}

export function expiryHint(days: number): string {
  if (days === 0) return "истекает сегодня";
  if (days > 0) return `осталось ${days} дн.`;
  return `просрочена на ${Math.abs(days)} дн.`;
}
