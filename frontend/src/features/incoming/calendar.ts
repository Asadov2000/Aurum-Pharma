const PHARMACY_TIME_ZONE = "Asia/Dushanbe";

const PHARMACY_DATE_FORMATTER = new Intl.DateTimeFormat("en-US", {
  timeZone: PHARMACY_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

export function pharmacyCalendarDate(now = new Date()): string {
  const parts = PHARMACY_DATE_FORMATTER.formatToParts(now);
  const values = new Map(parts.map((part) => [part.type, part.value]));
  return `${values.get("year")}-${values.get("month")}-${values.get("day")}`;
}
