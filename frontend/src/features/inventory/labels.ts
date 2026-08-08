import { type ExpiryStatus, type WriteOffReason } from "./types";

export const expiryLabel: Record<ExpiryStatus, string> = {
  expired: "Просрочена",
  red: "Красная зона",
  orange: "Оранжевая зона",
  yellow: "Жёлтая зона",
  normal: "Норма",
};

export const expiryTone: Record<
  ExpiryStatus,
  "neutral" | "success" | "warning" | "danger" | "info"
> = {
  expired: "danger",
  red: "danger",
  orange: "warning",
  yellow: "warning",
  normal: "success",
};

export const expiryOptions: ExpiryStatus[] = ["expired", "red", "orange", "yellow", "normal"];

/**
 * Map days-to-expiry to the shared ExpiryStatus buckets — the day-equivalents
 * of the backend's month thresholds (≤1 / ≤3 / ≤6 months). Used where only the
 * day count is on hand (e.g. POS cart lines) so colours stay consistent with
 * the batches screen. Returns null when the day count is unknown.
 */
export function expiryStatusFromDays(days: number | null | undefined): ExpiryStatus | null {
  if (days == null) return null;
  if (days <= 0) return "expired";
  if (days <= 30) return "red";
  if (days <= 90) return "orange";
  if (days <= 180) return "yellow";
  return "normal";
}

export const writeOffReasonLabel: Record<WriteOffReason, string> = {
  expired: "Просрочена",
  damaged: "Повреждение",
  spoiled: "Порча",
  theft: "Хищение",
  other: "Другое",
};

export const writeOffReasonOptions: WriteOffReason[] = [
  "expired",
  "damaged",
  "spoiled",
  "theft",
  "other",
];

// Movement-type labels — values come from the backend free-form
// constants in app/domains/inventory/service.py.
export const movementLabel: Record<string, string> = {
  incoming: "Поступление",
  sale: "Продажа",
  sale_return: "Возврат покупателя",
  write_off: "Списание",
  supplier_return: "Возврат поставщику",
  correction: "Корректировка",
  transfer_in: "Перемещение: приход",
  transfer_out: "Перемещение: расход",
};

export const movementSourceLabel: Record<string, string> = {
  incoming_document: "Приход",
  incoming_item: "Приход",
  sale: "Продажа",
  write_off: "Акт списания",
  supplier_return: "Возврат поставщику",
};
