const STORAGE_KEY = "aurum.refresh_operation_id";
const UUID_V4_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

let inMemoryOperationId: string | null = null;

function safeSessionStorage(): Storage | null {
  try {
    return window.sessionStorage;
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

export function getPendingRefreshOperationId(): string | null {
  const stored = safeSessionStorage()?.getItem(STORAGE_KEY) ?? inMemoryOperationId;
  if (stored && UUID_V4_PATTERN.test(stored)) {
    inMemoryOperationId = stored;
    return stored;
  }

  if (stored) {
    safeSessionStorage()?.removeItem(STORAGE_KEY);
  }
  inMemoryOperationId = null;
  return null;
}

export function getOrCreateRefreshOperationId(): string {
  const existing = getPendingRefreshOperationId();
  if (existing) return existing;

  const operationId = generateUuidV4();
  inMemoryOperationId = operationId;
  safeSessionStorage()?.setItem(STORAGE_KEY, operationId);
  return operationId;
}

export function clearRefreshOperationId(): void {
  inMemoryOperationId = null;
  safeSessionStorage()?.removeItem(STORAGE_KEY);
}
