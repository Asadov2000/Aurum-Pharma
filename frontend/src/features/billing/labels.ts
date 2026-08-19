import {
  type BillingPeriod,
  type FinancialInvoiceDisplayStatus,
  type SubscriptionStatus,
  type TenantFinancialInvoice,
} from "./types";

export function financialInvoiceStatus(
  invoice: TenantFinancialInvoice,
): FinancialInvoiceDisplayStatus {
  if (invoice.document_state === "void") return "void";
  if (invoice.settlement_state === "written_off") return "written_off";
  if (invoice.settlement_state === "paid") return "paid";
  if (invoice.collection_state === "overdue") return "overdue";
  if (invoice.settlement_state === "partially_paid") return "partially_paid";
  return "unpaid";
}

export const billingPeriodLabel: Record<BillingPeriod, string> = {
  monthly: "Помесячно",
  yearly: "Ежегодно",
};

export const subscriptionStatusLabel: Record<SubscriptionStatus, string> = {
  trial: "Пробный",
  active: "Активна",
  grace_period: "Льготный период",
  suspended: "Приостановлена",
  cancelled: "Отменена",
  archived: "В архиве",
};

export const subscriptionStatusTone: Record<
  SubscriptionStatus,
  "neutral" | "success" | "warning" | "danger" | "info"
> = {
  trial: "info",
  active: "success",
  grace_period: "warning",
  suspended: "danger",
  cancelled: "neutral",
  archived: "neutral",
};

export const financialInvoiceStatusLabel: Record<FinancialInvoiceDisplayStatus, string> = {
  unpaid: "Ожидает оплаты",
  partially_paid: "Оплачен частично",
  paid: "Оплачен",
  written_off: "Списан",
  overdue: "Просрочен",
  void: "Аннулирован",
};

export const financialInvoiceStatusTone: Record<
  FinancialInvoiceDisplayStatus,
  "neutral" | "success" | "warning" | "danger" | "info"
> = {
  unpaid: "info",
  partially_paid: "warning",
  paid: "success",
  written_off: "neutral",
  overdue: "danger",
  void: "neutral",
};
