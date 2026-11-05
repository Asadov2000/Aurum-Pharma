import { type PhysicalCartItemPut } from "./physicalCart";
import { posStorageKey, type PosStorageScope } from "./posStorageScope";

export type PhysicalCartRecovery =
  | { kind: "save"; itemId: string; name: string; payload: PhysicalCartItemPut }
  | { kind: "delete"; itemId: string };

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// This bootstrap reader stays independent of the lazily loaded form library.
function parse(value: unknown): PhysicalCartRecovery | null {
  if (!record(value) || typeof value.itemId !== "string" || !uuid.test(value.itemId)) return null;
  if (value.kind === "delete") return { kind: "delete", itemId: value.itemId };
  if (value.kind !== "save" || typeof value.name !== "string" || !record(value.payload))
    return null;
  const payload = value.payload;
  if (
    typeof payload.catalog_id !== "string" ||
    !uuid.test(payload.catalog_id) ||
    typeof payload.position !== "number" ||
    !Number.isSafeInteger(payload.position) ||
    payload.position < 1 ||
    typeof payload.quantity !== "number" ||
    !Number.isSafeInteger(payload.quantity) ||
    payload.quantity < 1 ||
    payload.quantity > 1_000_000 ||
    (payload.sale_format !== "pack" &&
      payload.sale_format !== "blister" &&
      payload.sale_format !== "unit") ||
    typeof payload.discount_amount !== "string" ||
    !/^\d+\.\d{2}$/.test(payload.discount_amount)
  )
    return null;
  return {
    kind: "save",
    itemId: value.itemId,
    name: value.name,
    payload: {
      catalog_id: payload.catalog_id,
      position: payload.position,
      sale_format: payload.sale_format,
      quantity: payload.quantity,
      discount_amount: payload.discount_amount,
    },
  };
}

function key(saleId: string, scope: PosStorageScope): string {
  return posStorageKey("aurum:physical-cart-recovery:v1:", saleId, scope);
}

/** Non-financial intent; keep its identity until server acknowledgment and refresh. */
export function loadPhysicalCartRecovery(
  saleId: string,
  scope: PosStorageScope,
): PhysicalCartRecovery | null {
  try {
    const raw = localStorage.getItem(key(saleId, scope));
    if (!raw) return null;
    return parse(JSON.parse(raw));
  } catch {
    return null;
  }
}

export function savePhysicalCartRecovery(
  saleId: string,
  scope: PosStorageScope,
  operation: PhysicalCartRecovery,
): void {
  const parsed = parse(operation);
  if (!parsed) throw new Error("Некорректная операция восстановления корзины");
  localStorage.setItem(key(saleId, scope), JSON.stringify(parsed));
}

export function clearPhysicalCartRecovery(saleId: string, scope: PosStorageScope): void {
  localStorage.removeItem(key(saleId, scope));
}
