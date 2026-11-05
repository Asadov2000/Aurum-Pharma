import { posStorageKey, type PosStorageScope } from "./posStorageScope";

export function unitPaymentMarkerKey(saleId: string, scope: PosStorageScope): string {
  return posStorageKey("pos:unitPaymentPreparation:", saleId, scope);
}
export function hasUnitPaymentPreparation(saleId: string, scope: PosStorageScope): boolean {
  try {
    return localStorage.getItem(unitPaymentMarkerKey(saleId, scope)) !== null;
  } catch {
    return true;
  }
}
