export const DEFAULT_REPORT_TIME_ZONE = "Asia/Dushanbe";

function calendarDateParts(value: Date, timeZone: string): Record<string, string> {
  let formatter: Intl.DateTimeFormat;
  try {
    formatter = new Intl.DateTimeFormat("en-US", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
  } catch {
    formatter = new Intl.DateTimeFormat("en-US", {
      timeZone: DEFAULT_REPORT_TIME_ZONE,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
  }
  return Object.fromEntries(
    formatter
      .formatToParts(value)
      .filter(({ type }) => type !== "literal")
      .map(({ type, value: partValue }) => [type, partValue]),
  );
}

export function calendarDateInTimeZone(value: Date, timeZone: string): string {
  const parts = calendarDateParts(value, timeZone);
  return `${parts.year}-${parts.month}-${parts.day}`;
}

export function addCalendarDays(value: string, days: number): string {
  const [yearPart = "", monthPart = "", dayPart = ""] = value.split("-");
  const year = Number(yearPart);
  const month = Number(monthPart);
  const day = Number(dayPart);
  const shifted = new Date(Date.UTC(year, month - 1, day + days));
  return shifted.toISOString().slice(0, 10);
}

export function recentReportRange(
  timeZone: string,
  now = new Date(),
): { dateFrom: string; dateTo: string } {
  const dateTo = calendarDateInTimeZone(now, timeZone);
  return {
    dateFrom: addCalendarDays(dateTo, -30),
    dateTo,
  };
}

export function currentReportMonthRange(
  timeZone: string,
  now = new Date(),
): { dateFrom: string; dateTo: string } {
  const dateTo = calendarDateInTimeZone(now, timeZone);
  return {
    dateFrom: `${dateTo.slice(0, 8)}01`,
    dateTo,
  };
}
