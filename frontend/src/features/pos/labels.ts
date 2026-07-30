import { type PaymentMethod, type SaleStatus, type ShiftStatus } from "./types";

export const paymentMethodLabel: Record<PaymentMethod, string> = {
  cash: "Наличные",
  card: "Карта",
  bank_transfer: "QR-код",
};

export const paymentMethodOptions: PaymentMethod[] = ["cash", "card", "bank_transfer"];

export const saleStatusLabel: Record<SaleStatus, string> = {
  draft: "Черновик",
  completed: "Завершена",
  voided: "Отменена",
};

export const saleStatusTone: Record<
  SaleStatus,
  "neutral" | "success" | "warning" | "danger" | "info"
> = {
  draft: "info",
  completed: "success",
  voided: "danger",
};

export const shiftStatusLabel: Record<ShiftStatus, string> = {
  open: "Открыта",
  closed: "Закрыта",
};
