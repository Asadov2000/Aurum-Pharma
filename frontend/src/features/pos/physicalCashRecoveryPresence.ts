import { posStorageKey, type PosStorageScope } from "./posStorageScope";

export function physicalCashRecoveryKey(saleId: string, scope: PosStorageScope): string {
  return posStorageKey("aurum:physical-cash-recovery:v1:", saleId, scope);
}

export function mixedCashRecoveryKey(saleId: string, scope: PosStorageScope): string {
  return posStorageKey("aurum:mixed-cash-recovery:v1:", saleId, scope);
}

export function hasMixedCashRecovery(saleId: string, scope: PosStorageScope): boolean {
  try {
    return window.localStorage.getItem(mixedCashRecoveryKey(saleId, scope)) !== null;
  } catch {
    return true;
  }
}

export function hasCashRecovery(saleId: string, scope: PosStorageScope): boolean {
  return hasPhysicalCashRecovery(saleId, scope) || hasMixedCashRecovery(saleId, scope);
}

/** Keep uncertain payments reachable without loading their validation or API code. */
export function hasPhysicalCashRecovery(saleId: string, scope: PosStorageScope): boolean {
  try {
    // Even unreadable or empty bytes may identify a payment already committed.
    return window.localStorage.getItem(physicalCashRecoveryKey(saleId, scope)) !== null;
  } catch {
    // Unavailable storage cannot prove that the financial operation is absent.
    return true;
  }
}
