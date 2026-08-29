export type RefundReasonCode =
  | "dispensing_error"
  | "duplicate_sale"
  | "pricing_error"
  | "quality_issue"
  | "damaged_package"
  | "customer_cancelled"
  | "other";

export const REFUND_REASON_OPTIONS: ReadonlyArray<{
  value: RefundReasonCode;
  label: string;
}> = [
  { value: "dispensing_error", label: "Ошибка при отпуске" },
  { value: "duplicate_sale", label: "Продажа проведена повторно" },
  { value: "pricing_error", label: "Ошибка цены или чека" },
  { value: "quality_issue", label: "Подозрение на качество" },
  { value: "damaged_package", label: "Повреждена упаковка" },
  { value: "customer_cancelled", label: "Отказ покупателя" },
  { value: "other", label: "Другая причина" },
];

export const REFUND_REASON_LABELS: Record<RefundReasonCode, string> = Object.fromEntries(
  REFUND_REASON_OPTIONS.map(({ value, label }) => [value, label]),
) as Record<RefundReasonCode, string>;

export function refundReasonLabel(reason: string | null): string {
  if (!reason) return "Не указана";
  return REFUND_REASON_LABELS[reason as RefundReasonCode] ?? "Причина из старой версии";
}
