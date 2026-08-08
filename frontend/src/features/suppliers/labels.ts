import { type SupplierReturnReason } from "./types";

export const supplierReturnReasonOptions: SupplierReturnReason[] = [
  "damaged",
  "expired",
  "incorrect_delivery",
  "quality_issue",
  "other",
];

export const supplierReturnReasonLabel: Record<SupplierReturnReason, string> = {
  damaged: "Повреждение",
  expired: "Истёкший срок",
  incorrect_delivery: "Ошибка поставки",
  quality_issue: "Проблема качества",
  other: "Другая причина",
};
