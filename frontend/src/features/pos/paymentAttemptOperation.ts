import { type PaymentMethod } from "./types";

const STORAGE_PREFIX = "pos:pendingPaymentAttempt:";
const UUID_V4_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const AMOUNT_PATTERN = /^(?:0|[1-9]\d{0,11})\.\d{2}$/;

export interface PendingPaymentAttemptOperation {
  operationId: string;
  saleId: string;
  paymentMethod: Extract<PaymentMethod, "card" | "qr">;
  amount: string;
}

const operationKey = (saleId: string): string => `${STORAGE_PREFIX}${saleId}`;

function safeLocalStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
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

function isOperation(value: unknown, saleId: string): value is PendingPaymentAttemptOperation {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<PendingPaymentAttemptOperation>;
  return (
    candidate.saleId === saleId &&
    typeof candidate.operationId === "string" &&
    UUID_V4_PATTERN.test(candidate.operationId) &&
    (candidate.paymentMethod === "card" || candidate.paymentMethod === "qr") &&
    typeof candidate.amount === "string" &&
    AMOUNT_PATTERN.test(candidate.amount) &&
    Number(candidate.amount) > 0
  );
}

export function loadPaymentAttemptOperation(saleId: string): PendingPaymentAttemptOperation | null {
  const storage = safeLocalStorage();
  try {
    const raw = storage?.getItem(operationKey(saleId));
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (isOperation(parsed, saleId)) return parsed;
    storage?.removeItem(operationKey(saleId));
  } catch {
    try {
      storage?.removeItem(operationKey(saleId));
    } catch {
      // The invalid marker cannot be used and in-memory state remains safe.
    }
  }
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
  const storage = safeLocalStorage();
  const serialized = JSON.stringify(operation);
  try {
    storage?.setItem(operationKey(saleId), serialized);
    return storage?.getItem(operationKey(saleId)) === serialized ? operation : null;
  } catch {
    return null;
  }
}

export function hasPaymentAttemptOperation(saleId: string): boolean {
  return loadPaymentAttemptOperation(saleId) !== null;
}

export function clearPaymentAttemptOperation(saleId: string, operationId?: string): void {
  const storage = safeLocalStorage();
  if (operationId) {
    const current = loadPaymentAttemptOperation(saleId);
    if (current?.operationId !== operationId) return;
  }
  try {
    storage?.removeItem(operationKey(saleId));
  } catch {
    // The server-side attempt remains authoritative.
  }
}
