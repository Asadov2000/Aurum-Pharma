import { z } from "zod";

export type QuickActionSection = "catalog" | "batches";
export interface QuickActionMenuPreferences {
  enabled: boolean;
  order: string[];
  hidden: string[];
}
export type QuickActionPreferences = Record<QuickActionSection, QuickActionMenuPreferences>;

export const QUICK_ACTION_DEFAULTS: QuickActionPreferences = {
  catalog: {
    enabled: true,
    order: ["open", "edit", "copy_name", "copy_barcode", "archive", "restore"],
    hidden: [],
  },
  batches: { enabled: true, order: ["open", "copy_number"], hidden: [] },
};

export const quickActionMenuSchema = z
  .object({
    enabled: z.boolean(),
    order: z.array(z.string().max(64)).max(32),
    hidden: z.array(z.string().max(64)).max(32),
  })
  .strict();

export function defaultQuickActionPreferences(
  section: QuickActionSection,
): QuickActionMenuPreferences {
  const defaults = QUICK_ACTION_DEFAULTS[section];
  return { enabled: defaults.enabled, order: [...defaults.order], hidden: [] };
}

export function parseQuickActionMenuPreferences(
  value: unknown,
  section: QuickActionSection,
): QuickActionMenuPreferences | null {
  const parsed = quickActionMenuSchema.safeParse(value);
  if (!parsed.success) return null;
  const allowed = new Set(QUICK_ACTION_DEFAULTS[section].order);
  const clean = (ids: string[]) => [...new Set(ids.filter((id) => allowed.has(id)))];
  const order = clean([...parsed.data.order, ...QUICK_ACTION_DEFAULTS[section].order]);
  const hidden = clean(parsed.data.hidden);
  if (order.every((id) => hidden.includes(id))) return null;
  return {
    enabled: parsed.data.enabled,
    order: [...order.filter((id) => id !== "archive"), ...order.filter((id) => id === "archive")],
    hidden,
  };
}

export function parseQuickActionPreferences(
  value: unknown,
): Partial<QuickActionPreferences> | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  if (Object.keys(record).length === 0) return null;
  if (Object.keys(record).some((key) => key !== "catalog" && key !== "batches")) return null;
  const result: Partial<QuickActionPreferences> = {};
  for (const section of ["catalog", "batches"] as const) {
    if (!(section in record)) continue;
    const config = parseQuickActionMenuPreferences(record[section], section);
    if (!config) return null;
    result[section] = config;
  }
  return result;
}

// Callers supply only commands already allowed by the current account and row state.
export function applyQuickActions<T extends { id: string; tone?: "default" | "danger" }>(
  items: T[],
  config: QuickActionMenuPreferences,
): T[] {
  const hidden = new Set(config.hidden);
  const positions = new Map(config.order.map((id, index) => [id, index]));
  return items
    .filter((item) => !hidden.has(item.id))
    .sort((left, right) => {
      const danger = Number(left.tone === "danger") - Number(right.tone === "danger");
      if (danger) return danger;
      return (positions.get(left.id) ?? Infinity) - (positions.get(right.id) ?? Infinity);
    });
}
