import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type MeResponse } from "@/features/auth/types";
import {
  captureRefundRecoveryContext,
  clearPendingRefundOperation,
  createPendingRefundOperation,
  hasUnresolvedRefundRecovery,
  isRefundRecoveryContextActive,
  loadPendingRefundOperation,
  pendingRefundOperationKey,
  savePendingRefundAttemptId,
  saveRecoveredPendingRefundOperation,
  type RefundRecoveryContext,
} from "@/features/sales/refundOperation";
import { type RefundAttempt } from "@/features/sales/types";
import { useAuthStore } from "@/stores/auth";

const authEpoch = vi.hoisted(() => ({ value: null as string | null }));
vi.mock("@/features/auth/logoutState", () => ({
  getAuthEpoch: () => authEpoch.value,
  isSessionRestorationBlocked: () => false,
}));

const SERVER_ATTEMPT: RefundAttempt = {
  id: "attempt-1",
  tenant_id: "tenant-1",
  parent_sale_id: "sale-1",
  register_id: "register-1",
  requested_by_user_id: "user-1",
  confirmed_by_user_id: null,
  operation_id: "11111111-1111-4111-8111-111111111111",
  items: [{ sale_item_id: "item-1", qty: "1" }],
  intent_locked: true,
  reason: "quality_issue",
  comment: "Упаковка повреждена",
  payments: [],
  total_amount: "10.00",
  external_amount: "10.00",
  currency: "TJS",
  status: "pending",
  void_reason: null,
  void_note: null,
  created_at: "2026-08-09T10:00:00Z",
  confirmed_at: null,
  consumed_at: null,
  voided_at: null,
};

const SALE = {
  id: "sale-1",
  tenant_id: "tenant-1",
  branch_id: "branch-1",
  register_id: "register-1",
};
const ITEMS = [{ sale_item_id: "item-1", qty: "1.000" }];

function signIn(userId = "user-1", tenantId = "tenant-1"): void {
  const user: MeResponse = {
    id: userId,
    email: "",
    full_name: "",
    is_developer: false,
    is_administrator: false,
    home_tenant_id: tenantId,
    active_tenant_id: tenantId,
    status: "active",
    last_login_at: null,
    level: 10,
    is_tenant_owner: false,
    branch_assignments: {},
    permissions: [],
    platform_capabilities: [],
    support_access: null,
  };
  useAuthStore.setState({ accessToken: "synthetic-access", user, hydrated: true });
}

function context(): RefundRecoveryContext {
  const captured = captureRefundRecoveryContext(SALE);
  if (!captured) throw new Error("Expected an authenticated recovery context");
  return captured;
}

function create(captured = context()) {
  const operation = createPendingRefundOperation(SALE.id, ITEMS, true, captured);
  if (!operation) throw new Error("Expected a persisted operation");
  return operation;
}

describe("owned pending refund journal", () => {
  beforeEach(() => {
    authEpoch.value = null;
    useAuthStore.getState().clear();
    window.localStorage.clear();
    signIn();
  });
  afterEach(() => vi.restoreAllMocks());

  it("persists technical scope and IDs without server notes or sensitive fields", () => {
    const captured = context();
    const recovered = saveRecoveredPendingRefundOperation(SALE.id, SERVER_ATTEMPT, captured);
    expect(recovered).toMatchObject({
      schemaVersion: 2,
      userId: "user-1",
      tenantId: SALE.tenant_id,
      parentSaleId: SALE.id,
      branchId: SALE.branch_id,
      registerId: SALE.register_id,
      refundAttemptOperationId: SERVER_ATTEMPT.operation_id,
      refundAttemptId: SERVER_ATTEMPT.id,
      items: SERVER_ATTEMPT.items,
    });
    expect(recovered?.operationId).toMatch(/^[0-9a-f-]{36}$/i);
    const raw = window.localStorage.getItem(pendingRefundOperationKey(SALE.id, captured));
    expect(JSON.parse(raw ?? "{}")).toEqual(recovered);
    expect(raw).not.toContain(SERVER_ATTEMPT.comment);
    expect(raw).not.toContain("synthetic-access");
    expect(hasUnresolvedRefundRecovery(SALE.id, captured)).toBe(false);
  });

  it("retains the original command and payload when creation is retried", () => {
    const captured = context();
    const operation = create(captured);
    expect(
      createPendingRefundOperation(
        SALE.id,
        [{ sale_item_id: "other", qty: "99" }],
        false,
        captured,
      ),
    ).toEqual(operation);
    expect(loadPendingRefundOperation(SALE.id, captured)).toEqual(operation);
  });

  it("clears only an owned record with the exact command ID", () => {
    const captured = context();
    const operation = create(captured);
    clearPendingRefundOperation(SALE.id, crypto.randomUUID(), captured);
    expect(loadPendingRefundOperation(SALE.id, captured)).toEqual(operation);
    clearPendingRefundOperation(SALE.id, operation.operationId, captured);
    expect(loadPendingRefundOperation(SALE.id, captured)).toBeNull();
  });

  it.each(["logout", "employee", "tenant"])("rejects every late mutator after %s", (change) => {
    const captured = context();
    const operation = create(captured);
    const key = pendingRefundOperationKey(SALE.id, captured);
    const bytes = window.localStorage.getItem(key);
    if (change === "logout") useAuthStore.getState().clear();
    if (change === "employee") signIn("user-2");
    if (change === "tenant") signIn("user-1", "tenant-2");
    expect(isRefundRecoveryContextActive(captured)).toBe(false);
    expect(loadPendingRefundOperation(SALE.id, captured)).toBeNull();
    expect(createPendingRefundOperation(SALE.id, ITEMS, true, captured)).toBeNull();
    expect(saveRecoveredPendingRefundOperation(SALE.id, SERVER_ATTEMPT, captured)).toBeNull();
    expect(savePendingRefundAttemptId(operation, "late-attempt", captured)).toBeNull();
    clearPendingRefundOperation(SALE.id, operation.operationId, captured);
    expect(hasUnresolvedRefundRecovery(SALE.id, captured)).toBe(true);
    expect(window.localStorage.getItem(key)).toBe(bytes);
  });

  it("isolates a second employee's command without deleting or accepting the first employee's IDs", () => {
    const a = context();
    const first = create(a);
    const keyA = pendingRefundOperationKey(SALE.id, a);
    const bytesA = window.localStorage.getItem(keyA);
    signIn("user-2");
    const b = context();
    expect(loadPendingRefundOperation(SALE.id, b)).toBeNull();
    expect(saveRecoveredPendingRefundOperation(SALE.id, SERVER_ATTEMPT, b)).toBeNull();
    expect(savePendingRefundAttemptId(first, "attempt-B", b)).toBeNull();
    clearPendingRefundOperation(SALE.id, first.operationId, b);
    const second = create(b);
    expect(second.operationId).not.toBe(first.operationId);
    expect(second.refundAttemptOperationId).not.toBe(first.refundAttemptOperationId);
    expect(second.userId).toBe("user-2");
    expect(window.localStorage.getItem(keyA)).toBe(bytesA);
    expect(pendingRefundOperationKey(SALE.id, b)).not.toBe(keyA);
  });

  it("preserves all three IDs across same employee logout and login but invalidates captured work", () => {
    const old = context();
    const created = create(old);
    const saved = savePendingRefundAttemptId(created, "attempt-1", old);
    expect(saved).not.toBeNull();
    useAuthStore.getState().clear();
    signIn();
    const current = context();
    expect(loadPendingRefundOperation(SALE.id, old)).toBeNull();
    expect(savePendingRefundAttemptId(created, "late-attempt", old)).toBeNull();
    clearPendingRefundOperation(SALE.id, created.operationId, old);
    expect(loadPendingRefundOperation(SALE.id, current)).toEqual(saved);
    expect(createPendingRefundOperation(SALE.id, ITEMS, true, current)).toEqual(saved);
  });

  it("keeps captured work valid during same authority token rotation", () => {
    const captured = context();
    const operation = create(captured);
    useAuthStore.setState({ accessToken: "synthetic-rotated" });
    expect(isRefundRecoveryContextActive(captured)).toBe(true);
    expect(savePendingRefundAttemptId(operation, "attempt-1", captured)?.operationId).toBe(
      operation.operationId,
    );
  });

  it("rejects late work when logout epoch changes before the user store changes", () => {
    const captured = context();
    const operation = create(captured);
    const bytes = window.localStorage.getItem(pendingRefundOperationKey(SALE.id, captured));
    authEpoch.value = "new-logout-epoch";
    expect(isRefundRecoveryContextActive(captured)).toBe(false);
    expect(savePendingRefundAttemptId(operation, "late-attempt", captured)).toBeNull();
    clearPendingRefundOperation(SALE.id, operation.operationId, captured);
    expect(window.localStorage.getItem(pendingRefundOperationKey(SALE.id, captured))).toBe(bytes);
  });

  it("uses a different persistent key when the same user enters another tenant", () => {
    const a = context();
    const operation = create(a);
    const key = pendingRefundOperationKey(SALE.id, a);
    const bytes = window.localStorage.getItem(key);
    signIn("user-1", "tenant-2");
    expect(captureRefundRecoveryContext(SALE)).toBeNull();
    const b = captureRefundRecoveryContext({ ...SALE, tenant_id: "tenant-2" });
    expect(b).not.toBeNull();
    expect(loadPendingRefundOperation(SALE.id, b)).toBeNull();
    const other = createPendingRefundOperation(SALE.id, ITEMS, true, b);
    expect(other?.operationId).not.toBe(operation.operationId);
    expect(other?.tenantId).toBe("tenant-2");
    expect(window.localStorage.getItem(key)).toBe(bytes);
  });

  it.each(["opaque-legacy", "{", ""])(
    "quarantines ownerless legacy bytes %j without adoption or removal",
    (raw) => {
      const captured = context();
      const legacyKey = `sales:pendingRefund:${SALE.id}`;
      window.localStorage.setItem(legacyKey, raw);
      expect(hasUnresolvedRefundRecovery(SALE.id, captured)).toBe(true);
      expect(loadPendingRefundOperation(SALE.id, captured)).toBeNull();
      expect(createPendingRefundOperation(SALE.id, ITEMS, true, captured)).toBeNull();
      expect(saveRecoveredPendingRefundOperation(SALE.id, SERVER_ATTEMPT, captured)).toBeNull();
      clearPendingRefundOperation(SALE.id, crypto.randomUUID(), captured);
      expect(window.localStorage.getItem(legacyKey)).toBe(raw);
      expect(window.localStorage.getItem(pendingRefundOperationKey(SALE.id, captured))).toBeNull();
    },
  );

  it.each(["{", "", JSON.stringify({ schemaVersion: 2, operationId: "invalid" })])(
    "preserves corrupt owned storage %j and blocks overwrite",
    (raw) => {
      const captured = context();
      const key = pendingRefundOperationKey(SALE.id, captured);
      window.localStorage.setItem(key, raw);
      expect(loadPendingRefundOperation(SALE.id, captured)).toBeNull();
      expect(hasUnresolvedRefundRecovery(SALE.id, captured)).toBe(true);
      expect(createPendingRefundOperation(SALE.id, ITEMS, true, captured)).toBeNull();
      expect(saveRecoveredPendingRefundOperation(SALE.id, SERVER_ATTEMPT, captured)).toBeNull();
      clearPendingRefundOperation(SALE.id, crypto.randomUUID(), captured);
      expect(window.localStorage.getItem(key)).toBe(raw);
    },
  );

  it.each(["tenantId", "userId", "parentSaleId", "branchId", "registerId"] as const)(
    "rejects stored %s scope mismatch without removing the record",
    (field) => {
      const captured = context();
      const operation = create(captured);
      const raw = JSON.stringify({ ...operation, [field]: "foreign" });
      const key = pendingRefundOperationKey(SALE.id, captured);
      window.localStorage.setItem(key, raw);
      expect(loadPendingRefundOperation(SALE.id, captured)).toBeNull();
      expect(createPendingRefundOperation(SALE.id, ITEMS, true, captured)).toBeNull();
      clearPendingRefundOperation(SALE.id, operation.operationId, captured);
      expect(window.localStorage.getItem(key)).toBe(raw);
    },
  );

  it("rejects changes to command, attempt command, or existing attempt ID", () => {
    const captured = context();
    const operation = create(captured);
    expect(
      savePendingRefundAttemptId(
        { ...operation, operationId: crypto.randomUUID() },
        "attempt-1",
        captured,
      ),
    ).toBeNull();
    expect(
      savePendingRefundAttemptId(
        { ...operation, refundAttemptOperationId: crypto.randomUUID() },
        "attempt-1",
        captured,
      ),
    ).toBeNull();
    const saved = savePendingRefundAttemptId(operation, "attempt-1", captured);
    expect(saved).not.toBeNull();
    expect(savePendingRefundAttemptId(operation, "attempt-2", captured)).toBeNull();
    expect(saved && savePendingRefundAttemptId(saved, "attempt-2", captured)).toBeNull();
    expect(saveRecoveredPendingRefundOperation(SALE.id, SERVER_ATTEMPT, captured)).toBeNull();
    expect(loadPendingRefundOperation(SALE.id, captured)).toEqual(saved);
  });

  it.each(["tenant_id", "register_id", "parent_sale_id", "requested_by_user_id"] as const)(
    "rejects server recovery with foreign %s",
    (field) => {
      expect(
        saveRecoveredPendingRefundOperation(
          SALE.id,
          { ...SERVER_ATTEMPT, [field]: "foreign" },
          context(),
        ),
      ).toBeNull();
    },
  );

  it("fails closed for unreadable storage and failed writes", () => {
    const captured = context();
    const read = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("Storage unavailable");
    });
    expect(hasUnresolvedRefundRecovery(SALE.id, captured)).toBe(true);
    expect(createPendingRefundOperation(SALE.id, ITEMS, true, captured)).toBeNull();
    read.mockRestore();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("Storage unavailable");
    });
    expect(createPendingRefundOperation(SALE.id, ITEMS, true, captured)).toBeNull();
  });

  it("rejects duplicate lines and invalid amounts before writing", () => {
    const captured = context();
    expect(
      createPendingRefundOperation(SALE.id, [ITEMS[0]!, ITEMS[0]!], true, captured),
    ).toBeNull();
    expect(
      createPendingRefundOperation(SALE.id, [{ sale_item_id: "item-1", qty: "0" }], true, captured),
    ).toBeNull();
    expect(window.localStorage.getItem(pendingRefundOperationKey(SALE.id, captured))).toBeNull();
  });
});
