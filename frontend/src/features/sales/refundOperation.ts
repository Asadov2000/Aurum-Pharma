import { type RefundLine } from "./types";

const STORAGE_PREFIX = "sales:pendingRefund:";
const UUID_V4_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export interface PendingRefundOperation {
  operationId: string;
  refundAttemptOperationId: string | null;
  refundAttemptId: string | null;
  parentSaleId: string;
  items: RefundLine[];
}

const refundOperationKey = (parentSaleId: string): string => `${STORAGE_PREFIX}${parentSaleId}`;

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

function isPendingRefundOperation(
  value: unknown,
  parentSaleId: string,
): value is PendingRefundOperation {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const candidate = value as Partial<PendingRefundOperation>;
  const validItems =
    Array.isArray(candidate.items) &&
    candidate.items.length > 0 &&
    candidate.items.length <= 200 &&
    candidate.items.every(isRefundLine) &&
    new Set(candidate.items.map((item) => item.sale_item_id)).size === candidate.items.length;
  return (
    candidate.parentSaleId === parentSaleId &&
    typeof candidate.operationId === "string" &&
    UUID_V4_PATTERN.test(candidate.operationId) &&
    validItems &&
    (candidate.refundAttemptOperationId === null ||
      (typeof candidate.refundAttemptOperationId === "string" &&
        UUID_V4_PATTERN.test(candidate.refundAttemptOperationId))) &&
    (candidate.refundAttemptId === null ||
      (typeof candidate.refundAttemptId === "string" &&
        candidate.refundAttemptId.length > 0 &&
        candidate.refundAttemptId.length <= 128))
  );
}

function isRefundLine(value: unknown): value is RefundLine {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const candidate = value as Partial<RefundLine>;
  if (
    typeof candidate.sale_item_id !== "string" ||
    candidate.sale_item_id.length === 0 ||
    candidate.sale_item_id.length > 128 ||
    typeof candidate.qty !== "string" ||
    !/^(?:0|[1-9]\d{0,10})(?:\.\d{1,3})?$/.test(candidate.qty)
  ) {
    return false;
  }
  const qty = Number(candidate.qty);
  return Number.isFinite(qty) && qty > 0;
}

export function loadPendingRefundOperation(parentSaleId: string): PendingRefundOperation | null {
  const storage = safeLocalStorage();
  try {
    const raw = storage?.getItem(refundOperationKey(parentSaleId));
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (isPendingRefundOperation(parsed, parentSaleId)) return parsed;
    storage?.removeItem(refundOperationKey(parentSaleId));
  } catch {
    try {
      storage?.removeItem(refundOperationKey(parentSaleId));
    } catch {
      // The invalid marker cannot be removed while storage is unavailable.
    }
  }
  return null;
}

export function createPendingRefundOperation(
  parentSaleId: string,
  items: RefundLine[],
  requiresExternalRefund: boolean,
): PendingRefundOperation | null {
  const existing = loadPendingRefundOperation(parentSaleId);
  if (existing) return existing;

  const operation: PendingRefundOperation = {
    operationId: generateUuidV4(),
    refundAttemptOperationId: requiresExternalRefund ? generateUuidV4() : null,
    refundAttemptId: null,
    parentSaleId,
    items: items.map((item) => ({ ...item })),
  };
  const storage = safeLocalStorage();
  const serialized = JSON.stringify(operation);
  try {
    storage?.setItem(refundOperationKey(parentSaleId), serialized);
    if (storage?.getItem(refundOperationKey(parentSaleId)) !== serialized) return null;
  } catch {
    return null;
  }
  return operation;
}

export function savePendingRefundAttemptId(
  operation: PendingRefundOperation,
  refundAttemptId: string,
): PendingRefundOperation | null {
  const stored = loadPendingRefundOperation(operation.parentSaleId);
  if (!stored || stored.operationId !== operation.operationId) return null;
  const updated = { ...stored, refundAttemptId };
  const storage = safeLocalStorage();
  const serialized = JSON.stringify(updated);
  try {
    storage?.setItem(refundOperationKey(operation.parentSaleId), serialized);
    if (storage?.getItem(refundOperationKey(operation.parentSaleId)) !== serialized) {
      return null;
    }
  } catch {
    return null;
  }
  return updated;
}

export function clearPendingRefundOperation(parentSaleId: string, operationId?: string): void {
  const storage = safeLocalStorage();
  try {
    if (
      operationId === undefined ||
      loadPendingRefundOperation(parentSaleId)?.operationId === operationId
    ) {
      storage?.removeItem(refundOperationKey(parentSaleId));
    }
  } catch {
    // A later reconciliation attempt can clear the marker.
  }
}

export function pendingRefundOperationKey(parentSaleId: string): string {
  return refundOperationKey(parentSaleId);
}
