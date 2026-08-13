import {
  type BillingPeriod,
  type InvoiceStatus,
  type PaymentMethod,
  type SubscriptionStatus,
} from "./types";

export const billingPeriodLabel: Record<BillingPeriod, string> = {
  monthly: "Помесячно",
  yearly: "Ежегодно",
};

export const paymentMethodLabel: Record<PaymentMethod, string> = {
  bank_transfer: "Банковский перевод",
  card: "Карта",
  cash: "Наличные",
};

export const paymentMethodOptions: PaymentMethod[] = ["bank_transfer", "card", "cash"];

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

export const invoiceStatusLabel: Record<InvoiceStatus, string> = {
  pending: "Ожидает оплаты",
  paid: "Оплачен",
  overdue: "Просрочен",
  cancelled: "Отменён",
};

export const invoiceStatusTone: Record<
  InvoiceStatus,
  "neutral" | "success" | "warning" | "danger" | "info"
> = {
  pending: "info",
  paid: "success",
  overdue: "danger",
  cancelled: "neutral",
};
