import { posStorageKey, type PosStorageScope } from "./posStorageScope";

export function trainingCheckoutKey(saleId: string, scope: PosStorageScope): string {
  return posStorageKey("aurum:training-checkout:v1:", saleId, scope);
}
export function hasTrainingCheckout(saleId: string, scope: PosStorageScope): boolean {
  try {
    return window.localStorage.getItem(trainingCheckoutKey(saleId, scope)) !== null;
  } catch {
    return true;
  }
}
