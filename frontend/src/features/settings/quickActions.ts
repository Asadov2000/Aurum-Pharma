export const QUICK_ACTION_SECTIONS = [
  "catalog",
  "batches",
  "incoming",
  "employees",
  "receipts",
  "pos_cart",
] as const;
export type QuickActionSection = (typeof QUICK_ACTION_SECTIONS)[number];
export const QUICK_ACTION_SECTION_LABELS: Record<QuickActionSection, string> = {
  catalog: "Каталог",
  batches: "Партии",
  incoming: "Приёмка товаров",
  employees: "Сотрудники",
  receipts: "Чеки и возвраты",
  pos_cart: "Касса — товары чека",
};
export const QUICK_ACTION_DANGER_IDS = new Set([
  "archive",
  "revoke_sessions",
  "suspend",
  "offboard",
  "transfer_ownership",
  "remove",
]);
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
  incoming: { enabled: true, order: ["open", "copy_number"], hidden: [] },
  receipts: { enabled: true, order: ["open", "copy_number"], hidden: [] },
  pos_cart: {
    enabled: true,
    order: ["copy_name", "copy_number", "increase_qty", "decrease_qty", "remove"],
    hidden: [],
  },
  employees: {
    enabled: true,
    order: [
      "open",
      "profile",
      "assign_access",
      "reissue_invitation",
      "activate",
      "revoke_sessions",
      "suspend",
      "offboard",
      "transfer_ownership",
    ],
    hidden: [],
  },
};

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
  if (!isQuickActionMenuPreferences(value)) return null;
  const allowed = new Set(QUICK_ACTION_DEFAULTS[section].order);
  const clean = (ids: string[]) => [...new Set(ids.filter((id) => allowed.has(id)))];
  const order = clean([...value.order, ...QUICK_ACTION_DEFAULTS[section].order]);
  const hidden = clean(value.hidden);
  if (order.every((id) => hidden.includes(id))) return null;
  return {
    enabled: value.enabled,
    order: [
      ...order.filter((id) => !QUICK_ACTION_DANGER_IDS.has(id)),
      ...order.filter((id) => QUICK_ACTION_DANGER_IDS.has(id)),
    ],
    hidden,
  };
}

// Row menus and their persisted preferences load on POS startup. Keep their
// bounded runtime validation independent of the settings form's Zod dependency.
function isQuickActionMenuPreferences(value: unknown): value is QuickActionMenuPreferences {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  for (const key in record) {
    if (key !== "enabled" && key !== "order" && key !== "hidden") return false;
  }
  return (
    typeof record.enabled === "boolean" &&
    isCommandList(record.order) &&
    isCommandList(record.hidden)
  );
}

function isCommandList(value: unknown): value is string[] {
  return (
    Array.isArray(value) &&
    value.length <= 32 &&
    Array.from(value).every((id: unknown) => typeof id === "string" && id.length <= 64)
  );
}

export function parseQuickActionPreferences(
  value: unknown,
): Partial<QuickActionPreferences> | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  if (Object.keys(record).length === 0) return null;
  if (Object.keys(record).some((key) => !QUICK_ACTION_SECTIONS.includes(key as QuickActionSection)))
    return null;
  const result: Partial<QuickActionPreferences> = {};
  for (const section of QUICK_ACTION_SECTIONS) {
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
