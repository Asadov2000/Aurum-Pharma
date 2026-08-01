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

const yearFormatter = new Intl.DateTimeFormat("ru-RU", {
  year: "numeric",
  timeZone: BILLING_TIME_ZONE,
});

const moneyFormatter = new Intl.NumberFormat("ru-RU", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function formatBillingDate(value: string): string {
  return dateFormatter.format(new Date(value));
}

export function formatBillingDateTime(value: string): string {
  return dateTimeFormatter.format(new Date(value));
}

export function billingYear(value: string): string {
  return yearFormatter.format(new Date(value));
}

export function formatBillingMoney(value: string | number, currency: string): string {
  return `${moneyFormatter.format(Number(value))} ${currency}`;
}
