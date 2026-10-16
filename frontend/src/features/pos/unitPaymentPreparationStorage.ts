import { type PosStorageScope } from "./posStorageScope";
import { unitPaymentMarkerKey } from "./unitPaymentPreparationPresence";

const uuid4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
export function loadUnitPaymentMarker(saleId: string, scope: PosStorageScope): string | null {
  const value = localStorage.getItem(unitPaymentMarkerKey(saleId, scope));
  if (value !== null && !uuid4.test(value))
    throw new Error("Код восстановления повреждён. Подготовка заблокирована.");
  return value;
}
export function storeUnitPaymentMarker(saleId: string, scope: PosStorageScope, id: string): void {
  if (!uuid4.test(id)) throw new Error("Некорректный код операции.");
  const previous = loadUnitPaymentMarker(saleId, scope);
  if (previous !== null && previous !== id)
    throw new Error("Сначала завершите проверку предыдущей подготовки.");
  localStorage.setItem(unitPaymentMarkerKey(saleId, scope), id);
  if (loadUnitPaymentMarker(saleId, scope) !== id)
    throw new Error("Не удалось сохранить код восстановления.");
}
export function clearUnitPaymentMarker(saleId: string, scope: PosStorageScope, id: string): void {
  if (loadUnitPaymentMarker(saleId, scope) === id) {
    localStorage.removeItem(unitPaymentMarkerKey(saleId, scope));
    if (loadUnitPaymentMarker(saleId, scope) !== null)
      throw new Error("Не удалось подтвердить отмену в этом браузере.");
  }
}
