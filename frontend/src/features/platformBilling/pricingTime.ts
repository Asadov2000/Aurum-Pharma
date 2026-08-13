const PRICING_TIME_ZONE = "Asia/Dushanbe";
const DUSHANBE_OFFSET = "+05:00";
const DAY_MS = 24 * 60 * 60 * 1000;

const localInputFormatter = new Intl.DateTimeFormat("en-CA", {
  timeZone: PRICING_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

const displayFormatter = new Intl.DateTimeFormat("ru-RU", {
  timeZone: PRICING_TIME_ZONE,
  dateStyle: "short",
  timeStyle: "short",
});

export function formatPricingDateTime(value: string): string {
  return displayFormatter.format(new Date(value));
}

export function minimumPricingLocalInput(noticeDays: number, now = Date.now()): string {
  return toPricingLocalInput(now + noticeDays * DAY_MS + 60_000);
}

export function parsePricingLocalInput(value: string): Date | null {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) return null;
  const parsed = new Date(`${value}:00${DUSHANBE_OFFSET}`);
  if (Number.isNaN(parsed.getTime()) || toPricingLocalInput(parsed.getTime()) !== value)
    return null;
  return parsed;
}

function toPricingLocalInput(timestamp: number): string {
  const parts = Object.fromEntries(
    localInputFormatter
      .formatToParts(new Date(timestamp))
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}
