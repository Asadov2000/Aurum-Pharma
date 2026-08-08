const BILLING_TIME_ZONE = "Asia/Dushanbe";

const dateFormatter = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  timeZone: BILLING_TIME_ZONE,
});

const dateTimeFormatter = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: BILLING_TIME_ZONE,
});

export function formatBillingDate(value: string): string {
  return dateFormatter.format(new Date(value));
}

export function formatBillingDateTime(value: string): string {
  return dateTimeFormatter.format(new Date(value));
}
