import { type PaymentMethod, type PaymentMethodRead } from "./types";
import { generateUuidV4, isUuidV4 } from "./operationId";
import { readStoredJson, removeStoredValue, writeStoredJson } from "./operationStorage";

const STORAGE_PREFIX = "pos:pendingPayment:";
const AMOUNT_PATTERN = /^\d+\.\d{2}$/;

export interface PendingPaymentOperation {
  operationId: string;
  saleId: string;
  paymentMethod: PaymentMethodRead;
  amount: string;
  metadata?: {
    cash_received?: string;
    external_confirmed?: true;
  };
}

const paymentOperationKey = (saleId: string): string => `${STORAGE_PREFIX}${saleId}`;

function isPaymentMethod(value: unknown): value is PaymentMethodRead {
  return value === "cash" || value === "card" || value === "qr" || value === "bank_transfer";
}

function isPendingPaymentOperation(
  value: unknown,
  saleId: string,
): value is PendingPaymentOperation {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<PendingPaymentOperation>;
  const validBase =
    candidate.saleId === saleId &&
    isUuidV4(candidate.operationId) &&
    isPaymentMethod(candidate.paymentMethod) &&
    typeof candidate.amount === "string" &&
    AMOUNT_PATTERN.test(candidate.amount) &&
    Number(candidate.amount) > 0;
  if (!validBase) return false;
  if (candidate.metadata === undefined) {
    return candidate.paymentMethod !== "card" && candidate.paymentMethod !== "qr";
  }
  if (
    !candidate.metadata ||
    typeof candidate.metadata !== "object" ||
    Array.isArray(candidate.metadata)
  ) {
    return false;
  }
  if (candidate.paymentMethod === "cash") {
    if (Object.keys(candidate.metadata).some((key) => key !== "cash_received")) return false;
    const cashReceived = candidate.metadata.cash_received;
    return (
      typeof cashReceived === "string" &&
      AMOUNT_PATTERN.test(cashReceived) &&
      Number(cashReceived) >= Number(candidate.amount)
    );
  }
  if (candidate.paymentMethod === "card" || candidate.paymentMethod === "qr") {
    return (
      Object.keys(candidate.metadata).every((key) => key === "external_confirmed") &&
      candidate.metadata.external_confirmed === true
    );
  }
  return false;
}

export function loadPendingPaymentOperation(saleId: string): PendingPaymentOperation | null {
  const parsed = readStoredJson(paymentOperationKey(saleId));
  if (isPendingPaymentOperation(parsed, saleId)) return parsed;
  if (parsed !== null) removeStoredValue(paymentOperationKey(saleId));
  return null;
}

export function createPendingPaymentOperation(
  saleId: string,
  paymentMethod: PaymentMethod,
  amount: string,
  metadata?: { cash_received?: string; external_confirmed?: true },
): PendingPaymentOperation | null {
  const operation: PendingPaymentOperation = {
    operationId: generateUuidV4(),
    saleId,
    paymentMethod,
    amount,
    metadata,
  };
  if (!isPendingPaymentOperation(operation, saleId)) return null;
  return writeStoredJson(paymentOperationKey(saleId), operation) ? operation : null;
}

export function clearPendingPaymentOperation(saleId: string, operationId?: string): void {
  if (!operationId) {
    removeStoredValue(paymentOperationKey(saleId));
    return;
  }

  const current = loadPendingPaymentOperation(saleId);
  if (current?.operationId === operationId) {
    removeStoredValue(paymentOperationKey(saleId));
  }
}
