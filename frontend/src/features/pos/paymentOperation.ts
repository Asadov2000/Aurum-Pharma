import { type PaymentMethod, type PaymentMethodRead } from "./types";

const STORAGE_PREFIX = "pos:pendingPayment:";
const UUID_V4_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
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

function safeLocalStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function removeStoredOperation(storage: Storage | null, saleId: string): void {
  try {
    storage?.removeItem(paymentOperationKey(saleId));
  } catch {
    // Keep the in-memory operation usable when browser storage is unavailable.
  }
}

function generateUuidV4(): string {
  if (typeof globalThis.crypto.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }

  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6]! & 0x0f) | 0x40;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0"));
  return [
    hex.slice(0, 4).join(""),
    hex.slice(4, 6).join(""),
    hex.slice(6, 8).join(""),
    hex.slice(8, 10).join(""),
    hex.slice(10, 16).join(""),
  ].join("-");
}

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
    typeof candidate.operationId === "string" &&
    UUID_V4_PATTERN.test(candidate.operationId) &&
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
  const storage = safeLocalStorage();
  try {
    const raw = storage?.getItem(paymentOperationKey(saleId));
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (isPendingPaymentOperation(parsed, saleId)) return parsed;
    removeStoredOperation(storage, saleId);
  } catch {
    removeStoredOperation(storage, saleId);
  }
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
  const storage = safeLocalStorage();
  const serialized = JSON.stringify(operation);
  try {
    storage?.setItem(paymentOperationKey(saleId), serialized);
    if (storage?.getItem(paymentOperationKey(saleId)) !== serialized) return null;
  } catch {
    return null;
  }
  return operation;
}

export function clearPendingPaymentOperation(saleId: string, operationId?: string): void {
  const storage = safeLocalStorage();
  if (!operationId) {
    removeStoredOperation(storage, saleId);
    return;
  }

  const current = loadPendingPaymentOperation(saleId);
  if (current?.operationId === operationId) {
    removeStoredOperation(storage, saleId);
  }
}

export function hasPendingPaymentOperation(saleId: string): boolean {
  return loadPendingPaymentOperation(saleId) !== null;
}
