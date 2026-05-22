import { type ExpiryStatus, type WriteOffReason } from "./types";

export const expiryLabel: Record<ExpiryStatus, string> = {
  expired: "Просрочена",
  red: "Красная зона",
  orange: "Оранжевая зона",
  yellow: "Жёлтая зона",
  normal: "Норма",
};

export const expiryTone: Record<ExpiryStatus, "neutral" | "success" | "warning" | "danger" | "info"> = {
  expired: "danger",
  red: "danger",
  orange: "warning",
  yellow: "warning",
  normal: "success",
};

export const expiryOptions: ExpiryStatus[] = ["expired", "red", "orange", "yellow", "normal"];

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
  receipt: "Поступление",
  sale: "Продажа",
  return: "Возврат",
  write_off: "Списание",
  reservation_hold: "Резерв",
  reservation_release: "Снятие резерва",
  adjustment: "Корректировка",
};
