import { getAuthEpoch } from "@/features/auth/logoutState";
import { posStorageKey } from "@/features/pos/posStorageScope";
import { type SaleDetails } from "@/features/pos/types";
import { getAuthSessionGeneration, useAuthStore } from "@/stores/auth";

import { type RefundAttempt, type RefundLine } from "./types";

const STORAGE_PREFIX = "sales:pendingRefund:";
const UUID_V4_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export interface PendingRefundOperation {
  schemaVersion: 2;
  tenantId: string;
  userId: string;
  branchId: string;
  registerId: string;
  operationId: string;
  refundAttemptOperationId: string | null;
  refundAttemptId: string | null;
  parentSaleId: string;
  items: RefundLine[];
}

export interface RefundRecoveryContext {
  readonly tenantId: string;
  readonly userId: string;
  readonly parentSaleId: string;
  readonly branchId: string;
  readonly registerId: string;
  readonly generation: number;
  readonly epoch: string | null;
}

export function captureRefundRecoveryContext(
  sale: Pick<SaleDetails, "id" | "tenant_id" | "branch_id" | "register_id">,
): RefundRecoveryContext | null {
  const { user, accessToken } = useAuthStore.getState();
  if (!user || !accessToken || user.active_tenant_id !== sale.tenant_id) return null;
  return Object.freeze({
    tenantId: sale.tenant_id,
    userId: user.id,
    parentSaleId: sale.id,
    branchId: sale.branch_id,
    registerId: sale.register_id,
    generation: getAuthSessionGeneration(),
    epoch: getAuthEpoch(),
  });
}

export function isRefundRecoveryContextActive(
  context: RefundRecoveryContext | null,
): context is RefundRecoveryContext {
  const { user, accessToken } = useAuthStore.getState();
  return !!(
    context &&
    accessToken &&
    user &&
    context.generation === getAuthSessionGeneration() &&
    context.epoch === getAuthEpoch() &&
    user.id === context.userId &&
    user.active_tenant_id === context.tenantId
  );
}

function validContext(
  parentSaleId: string,
  context: RefundRecoveryContext | null,
): context is RefundRecoveryContext {
  return isRefundRecoveryContextActive(context) && context.parentSaleId === parentSaleId;
}

export function pendingRefundOperationKey(
  parentSaleId: string,
  context: RefundRecoveryContext,
): string {
  return posStorageKey(`${STORAGE_PREFIX}v2:`, parentSaleId, context);
}

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
  context: RefundRecoveryContext,
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
    candidate.schemaVersion === 2 &&
    candidate.tenantId === context.tenantId &&
    candidate.userId === context.userId &&
    candidate.parentSaleId === context.parentSaleId &&
    candidate.branchId === context.branchId &&
    candidate.registerId === context.registerId &&
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

type StoredRecovery =
  | { status: "missing" }
  | { status: "blocked" }
  | { status: "owned"; operation: PendingRefundOperation };

function readRecovery(parentSaleId: string, context: RefundRecoveryContext | null): StoredRecovery {
  if (!validContext(parentSaleId, context)) return { status: "blocked" };
  const storage = safeLocalStorage();
  if (!storage) return { status: "blocked" };
  try {
    // Ownerless records may represent a real payout. Never adopt or erase them.
    if (storage.getItem(`${STORAGE_PREFIX}${parentSaleId}`) !== null) {
      return { status: "blocked" };
    }
    const raw = storage.getItem(pendingRefundOperationKey(parentSaleId, context));
    if (raw === null) return { status: "missing" };
    const parsed: unknown = JSON.parse(raw);
    return isPendingRefundOperation(parsed, context)
      ? { status: "owned", operation: parsed }
      : { status: "blocked" };
  } catch {
    // Unreadable is not proof that a payment did not happen.
    return { status: "blocked" };
  }
}

export function hasUnresolvedRefundRecovery(
  parentSaleId: string,
  context: RefundRecoveryContext | null,
): boolean {
  return readRecovery(parentSaleId, context).status === "blocked";
}

export function loadPendingRefundOperation(
  parentSaleId: string,
  context: RefundRecoveryContext | null,
): PendingRefundOperation | null {
  const result = readRecovery(parentSaleId, context);
  return result.status === "owned" ? result.operation : null;
}

function persistOperation(
  operation: PendingRefundOperation,
  context: RefundRecoveryContext | null,
): PendingRefundOperation | null {
  if (
    !validContext(operation.parentSaleId, context) ||
    !isPendingRefundOperation(operation, context)
  ) {
    return null;
  }
  const storage = safeLocalStorage();
  if (!storage) return null;
  try {
    const serialized = JSON.stringify(operation);
    const key = pendingRefundOperationKey(operation.parentSaleId, context);
    storage.setItem(key, serialized);
    return storage.getItem(key) === serialized && isRefundRecoveryContextActive(context)
      ? operation
      : null;
  } catch {
    return null;
  }
}

function operationScope(context: RefundRecoveryContext) {
  return {
    schemaVersion: 2 as const,
    tenantId: context.tenantId,
    userId: context.userId,
    parentSaleId: context.parentSaleId,
    branchId: context.branchId,
    registerId: context.registerId,
  };
}

export function createPendingRefundOperation(
  parentSaleId: string,
  items: RefundLine[],
  requiresExternalRefund: boolean,
  context: RefundRecoveryContext | null,
): PendingRefundOperation | null {
  if (!validContext(parentSaleId, context)) return null;
  const result = readRecovery(parentSaleId, context);
  if (result.status === "blocked") return null;
  if (result.status === "owned") return result.operation;
  return persistOperation(
    {
      ...operationScope(context),
      operationId: generateUuidV4(),
      refundAttemptOperationId: requiresExternalRefund ? generateUuidV4() : null,
      refundAttemptId: null,
      items: items.map((item) => ({ ...item })),
    },
    context,
  );
}

export function saveRecoveredPendingRefundOperation(
  parentSaleId: string,
  attempt: RefundAttempt,
  context: RefundRecoveryContext | null,
): PendingRefundOperation | null {
  if (
    !validContext(parentSaleId, context) ||
    attempt.tenant_id !== context.tenantId ||
    attempt.requested_by_user_id !== context.userId ||
    attempt.register_id !== context.registerId ||
    attempt.parent_sale_id !== parentSaleId ||
    !UUID_V4_PATTERN.test(attempt.operation_id) ||
    !attempt.id ||
    attempt.id.length > 128 ||
    attempt.items.length === 0 ||
    attempt.items.length > 200 ||
    !attempt.items.every(isRefundLine) ||
    new Set(attempt.items.map((item) => item.sale_item_id)).size !== attempt.items.length
  )
    return null;
  const result = readRecovery(parentSaleId, context);
  if (result.status === "blocked") return null;
  if (result.status === "owned") {
    // An existing command and its immutable bank attempt must stay together.
    return result.operation.refundAttemptOperationId === attempt.operation_id &&
      (result.operation.refundAttemptId === null ||
        result.operation.refundAttemptId === attempt.id) &&
      result.operation.items.length === attempt.items.length &&
      result.operation.items.every((item) =>
        attempt.items.some(
          (serverItem) =>
            serverItem.sale_item_id === item.sale_item_id &&
            Number(serverItem.qty) === Number(item.qty),
        ),
      )
      ? result.operation
      : null;
  }
  return persistOperation(
    {
      ...operationScope(context),
      operationId: generateUuidV4(),
      refundAttemptOperationId: attempt.operation_id,
      refundAttemptId: attempt.id,
      items: attempt.items.map((item) => ({ ...item })),
    },
    context,
  );
}

export function savePendingRefundAttemptId(
  operation: PendingRefundOperation,
  refundAttemptId: string,
  context: RefundRecoveryContext | null,
): PendingRefundOperation | null {
  if (
    !validContext(operation.parentSaleId, context) ||
    !isPendingRefundOperation(operation, context)
  ) {
    return null;
  }
  const stored = loadPendingRefundOperation(operation.parentSaleId, context);
  if (
    !stored ||
    stored.operationId !== operation.operationId ||
    stored.refundAttemptOperationId !== operation.refundAttemptOperationId ||
    stored.refundAttemptId !== operation.refundAttemptId ||
    !stored.refundAttemptOperationId ||
    (stored.refundAttemptId !== null && stored.refundAttemptId !== refundAttemptId)
  )
    return null;
  return persistOperation({ ...stored, refundAttemptId }, context);
}

export function clearPendingRefundOperation(
  parentSaleId: string,
  operationId: string,
  context: RefundRecoveryContext | null,
): void {
  if (!validContext(parentSaleId, context)) return;
  const stored = loadPendingRefundOperation(parentSaleId, context);
  if (!stored || stored.operationId !== operationId) return;
  try {
    safeLocalStorage()?.removeItem(pendingRefundOperationKey(parentSaleId, context));
  } catch {
    // Preserve IDs for a later reconciliation when storage becomes available.
  }
}
