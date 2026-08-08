import {
  type PaymentMethod,
  type PaymentMethodRead,
  type SaleStatus,
  type ShiftStatus,
} from "./types";

export const paymentMethodLabel: Record<PaymentMethodRead, string> = {
  cash: "Наличные",
  card: "Карта",
  qr: "QR-код",
  bank_transfer: "Банковский перевод",
};

export const paymentMethodOptions: PaymentMethod[] = ["cash", "card", "qr"];

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
