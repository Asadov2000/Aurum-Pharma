import { readStoredValue, removeStoredValue, writeStoredValue } from "./operationStorage";

const STORAGE_PREFIX = "pos:pendingCompletion:";

const completionOperationKey = (saleId: string): string => `${STORAGE_PREFIX}${saleId}`;

export function hasPendingCompletion(saleId: string): boolean {
  return readStoredValue(completionOperationKey(saleId)) === saleId;
}

export function markPendingCompletion(saleId: string): boolean {
  return writeStoredValue(completionOperationKey(saleId), saleId);
}

export function clearPendingCompletion(saleId: string): void {
  removeStoredValue(completionOperationKey(saleId));
}
