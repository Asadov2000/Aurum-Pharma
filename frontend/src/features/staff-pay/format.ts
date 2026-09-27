export function payMoney(value: string): string {
  return `${new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(value))} TJS`;
}
export function payDate(value: string): string {
  return new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeZone: "UTC" }).format(
    new Date(`${value}T00:00:00Z`),
  );
}
export function payDateTime(value: string, timezone: string): string {
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: timezone,
  }).format(new Date(value));
}
export function pharmacyToday(timezone: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const get = (part: string) => parts.find((value) => value.type === part)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}
