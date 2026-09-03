import { type PaymentAttempt, type PaymentMethod } from "./types";
import { generateUuidV4, isUuidV4 } from "./operationId";
import { readStoredJson, removeStoredValue, writeStoredJson } from "./operationStorage";

const STORAGE_PREFIX = "pos:pendingPaymentAttempt:";
const AMOUNT_PATTERN = /^(?:0|[1-9]\d{0,11})\.\d{2}$/;

export interface PendingPaymentAttemptOperation {
  operationId: string;
  saleId: string;
  paymentMethod: Extract<PaymentMethod, "card" | "qr">;
  amount: string;
}

const operationKey = (saleId: string): string => `${STORAGE_PREFIX}${saleId}`;

function isOperation(value: unknown, saleId: string): value is PendingPaymentAttemptOperation {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<PendingPaymentAttemptOperation>;
  return (
    candidate.saleId === saleId &&
    isUuidV4(candidate.operationId) &&
    (candidate.paymentMethod === "card" || candidate.paymentMethod === "qr") &&
    typeof candidate.amount === "string" &&
    AMOUNT_PATTERN.test(candidate.amount) &&
    Number(candidate.amount) > 0
  );
}

export function loadPaymentAttemptOperation(saleId: string): PendingPaymentAttemptOperation | null {
  const parsed = readStoredJson(operationKey(saleId));
  if (isOperation(parsed, saleId)) return parsed;
  if (parsed !== null) removeStoredValue(operationKey(saleId));
  return null;
}

export function createPaymentAttemptOperation(
  saleId: string,
  paymentMethod: Extract<PaymentMethod, "card" | "qr">,
  amount: string,
): PendingPaymentAttemptOperation | null {
  const existing = loadPaymentAttemptOperation(saleId);
  if (existing && existing.paymentMethod === paymentMethod && existing.amount === amount) {
    return existing;
  }
  if (existing) return null;
  const operation: PendingPaymentAttemptOperation = {
    operationId: generateUuidV4(),
    saleId,
    paymentMethod,
    amount,
  };
  if (!isOperation(operation, saleId)) return null;
  return writeStoredJson(operationKey(saleId), operation) ? operation : null;
}

export function saveRecoveredPaymentAttemptOperation(
  attempt: PaymentAttempt,
): PendingPaymentAttemptOperation | null {
  if (
    !isUuidV4(attempt.operation_id) ||
    (attempt.payment_method !== "card" && attempt.payment_method !== "qr") ||
    !AMOUNT_PATTERN.test(attempt.amount) ||
    Number(attempt.amount) <= 0
  ) {
    return null;
  }
  const existing = loadPaymentAttemptOperation(attempt.sale_id);
  if (existing) {
    return existing.operationId === attempt.operation_id &&
      existing.paymentMethod === attempt.payment_method &&
      existing.amount === attempt.amount
      ? existing
      : null;
  }
  const operation: PendingPaymentAttemptOperation = {
    operationId: attempt.operation_id,
    saleId: attempt.sale_id,
    paymentMethod: attempt.payment_method,
    amount: attempt.amount,
  };
  return writeStoredJson(operationKey(attempt.sale_id), operation) ? operation : null;
}

export function hasPaymentAttemptOperation(saleId: string): boolean {
  return loadPaymentAttemptOperation(saleId) !== null;
}

export function clearPaymentAttemptOperation(saleId: string, operationId?: string): void {
  if (operationId) {
    const current = loadPaymentAttemptOperation(saleId);
    if (current?.operationId !== operationId) return;
  }
  removeStoredValue(operationKey(saleId));
}
