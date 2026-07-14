const STORAGE_PREFIX = "pos:pendingCheckout:";
const UUID_V4_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export interface PendingCheckoutOperation {
  operationId: string;
  saleId: string;
  registerId: string;
}

const checkoutOperationKey = (saleId: string): string => `${STORAGE_PREFIX}${saleId}`;

function safeLocalStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function removeStoredOperation(storage: Storage | null, saleId: string): void {
  try {
    storage?.removeItem(checkoutOperationKey(saleId));
  } catch {
    // The in-memory marker remains usable if browser storage becomes unavailable.
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

function isPendingCheckoutOperation(
  value: unknown,
  saleId: string,
): value is PendingCheckoutOperation {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<PendingCheckoutOperation>;
  return (
    candidate.saleId === saleId &&
    typeof candidate.registerId === "string" &&
    candidate.registerId.length > 0 &&
    typeof candidate.operationId === "string" &&
    UUID_V4_PATTERN.test(candidate.operationId)
  );
}

export function loadPendingCheckoutOperation(
  saleId: string,
  registerId?: string,
): PendingCheckoutOperation | null {
  const storage = safeLocalStorage();
  try {
    const raw = storage?.getItem(checkoutOperationKey(saleId));
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (isPendingCheckoutOperation(parsed, saleId)) {
      return registerId === undefined || parsed.registerId === registerId ? parsed : null;
    }
    removeStoredOperation(storage, saleId);
  } catch {
    removeStoredOperation(storage, saleId);
  }
  return null;
}

export function createPendingCheckoutOperation(
  saleId: string,
  registerId: string,
): PendingCheckoutOperation | null {
  const operation: PendingCheckoutOperation = {
    operationId: generateUuidV4(),
    saleId,
    registerId,
  };
  const storage = safeLocalStorage();
  const serialized = JSON.stringify(operation);
  try {
    storage?.setItem(checkoutOperationKey(saleId), serialized);
    if (storage?.getItem(checkoutOperationKey(saleId)) !== serialized) return null;
  } catch {
    return null;
  }
  return operation;
}

export function clearPendingCheckoutOperation(saleId: string, operationId?: string): void {
  const storage = safeLocalStorage();
  if (!operationId) {
    removeStoredOperation(storage, saleId);
    return;
  }

  const current = loadPendingCheckoutOperation(saleId);
  if (current?.operationId === operationId) {
    removeStoredOperation(storage, saleId);
  }
}

export function hasPendingCheckoutOperation(saleId: string): boolean {
  return loadPendingCheckoutOperation(saleId) !== null;
}
