import { posStorageKey, type PosStorageScope } from "./posStorageScope";

export function physicalCashRecoveryKey(saleId: string, scope: PosStorageScope): string {
  return posStorageKey("aurum:physical-cash-recovery:v1:", saleId, scope);
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
