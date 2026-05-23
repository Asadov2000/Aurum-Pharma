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
  past_due: "Просрочена",
  cancelled: "Отменена",
  expired: "Истекла",
};

export const subscriptionStatusTone: Record<
  SubscriptionStatus,
  "neutral" | "success" | "warning" | "danger" | "info"
> = {
  trial: "info",
  active: "success",
  past_due: "warning",
  cancelled: "neutral",
  expired: "danger",
};

export const invoiceStatusLabel: Record<InvoiceStatus, string> = {
  open: "Открыт",
  paid: "Оплачен",
  void: "Аннулирован",
  overdue: "Просрочен",
};

export const invoiceStatusTone: Record<
  InvoiceStatus,
  "neutral" | "success" | "warning" | "danger" | "info"
> = {
  open: "info",
  paid: "success",
  void: "neutral",
  overdue: "danger",
};
