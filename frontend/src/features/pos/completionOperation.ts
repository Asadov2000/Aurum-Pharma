const STORAGE_PREFIX = "pos:pendingCompletion:";

const completionOperationKey = (saleId: string): string => `${STORAGE_PREFIX}${saleId}`;

function safeLocalStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function hasPendingCompletion(saleId: string): boolean {
  try {
    return safeLocalStorage()?.getItem(completionOperationKey(saleId)) === saleId;
  } catch {
    return false;
  }
}

export function markPendingCompletion(saleId: string): boolean {
  const storage = safeLocalStorage();
  try {
    storage?.setItem(completionOperationKey(saleId), saleId);
    return storage?.getItem(completionOperationKey(saleId)) === saleId;
  } catch {
    return false;
  }
}

export function clearPendingCompletion(saleId: string): void {
  try {
    safeLocalStorage()?.removeItem(completionOperationKey(saleId));
  } catch {
    // Storage may be unavailable; there is no authorization value in this marker.
  }
}
