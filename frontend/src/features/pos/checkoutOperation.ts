import { generateUuidV4, isUuidV4 } from "./operationId";
import { readStoredJson, removeStoredValue, writeStoredJson } from "./operationStorage";

const STORAGE_PREFIX = "pos:pendingCheckout:";

export interface PendingCheckoutOperation {
  operationId: string;
  saleId: string;
  registerId: string;
}

const checkoutOperationKey = (saleId: string): string => `${STORAGE_PREFIX}${saleId}`;

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
    isUuidV4(candidate.operationId)
  );
}

export function loadPendingCheckoutOperation(
  saleId: string,
  registerId?: string,
): PendingCheckoutOperation | null {
  const parsed = readStoredJson(checkoutOperationKey(saleId));
  if (isPendingCheckoutOperation(parsed, saleId)) {
    return registerId === undefined || parsed.registerId === registerId ? parsed : null;
  }
  if (parsed !== null) removeStoredValue(checkoutOperationKey(saleId));
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
  return writeStoredJson(checkoutOperationKey(saleId), operation) ? operation : null;
}

export function clearPendingCheckoutOperation(saleId: string, operationId?: string): void {
  if (!operationId) {
    removeStoredValue(checkoutOperationKey(saleId));
    return;
  }

  const current = loadPendingCheckoutOperation(saleId);
  if (current?.operationId === operationId) {
    removeStoredValue(checkoutOperationKey(saleId));
  }
}
