import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PendingPosCommand } from "@/features/pos/commandOperation";
import type { SavedDraft } from "@/features/pos/draftStorage";
import type { PosStorageScope } from "@/features/pos/posStorageScope";
import type { PosCommandResult, SaleDetails } from "@/features/pos/types";

const apiMocks = vi.hoisted(() => ({
  getSale: vi.fn<(saleId: string) => Promise<SaleDetails>>(),
  getPosCommandResult: vi.fn<(operationId: string) => Promise<PosCommandResult>>(),
  createSale: vi.fn(),
  addSaleItem: vi.fn(),
  updateSaleItem: vi.fn(),
  deleteSaleItem: vi.fn(),
}));

vi.mock("@/features/pos/api", () => apiMocks);

import { pendingPosCommandKey } from "@/features/pos/commandOperation";
import { markPendingCompletion } from "@/features/pos/completionOperation";
import { preparePosRecovery } from "@/features/pos/draftRecovery";
import { draftKey } from "@/features/pos/draftStorage";
import { createPaymentAttemptOperation } from "@/features/pos/paymentAttemptOperation";

const REGISTER_ID = "register-1";
const SALE_ID = "10000000-0000-4000-8000-000000000001";
const OPERATION_ID = "20000000-0000-4000-8000-000000000001";
const ATTEMPT_ID = "30000000-0000-4000-8000-000000000001";
const SCOPE: PosStorageScope = { tenantId: "tenant-1", userId: "cashier-1" };
const LEGACY_DRAFT_KEY = `pos:currentSale:${REGISTER_ID}`;
const LEGACY_COMMAND_KEY = `pos:pendingCommand:v1:${REGISTER_ID}`;
const SALE: SaleDetails = {
  id: SALE_ID,
  tenant_id: SCOPE.tenantId,
  branch_id: "branch-1",
  register_id: REGISTER_ID,
  shift_id: "shift-1",
  sale_type: "sale",
  parent_sale_id: null,
  status: "draft",
  receipt_number: null,
  operation_id: null,
  is_test: false,
  total_amount: "10.00",
  currency: "TJS",
  voided_at: null,
  voided_by_sale_id: null,
  cashier_user_id: SCOPE.userId,
  created_at: "2026-09-01T10:00:00Z",
  completed_at: null,
  items: [],
  payments: [],
};
const DRAFT: SavedDraft = {
  saleId: SALE_ID,
  nameById: { "catalog-1": "Аспирин" },
  savedAt: Date.parse("2026-09-01T10:00:00Z"),
  status: "draft",
  requiresRx: true,
  stagedPayments: [{ payment_method: "card", amount: "10.00", payment_attempt_id: ATTEMPT_ID }],
  expiredSaleConfirmed: true,
  externalPaymentReviewRequired: true,
};
const COMMAND_RESULT: PosCommandResult = {
  operation_id: OPERATION_ID,
  sale_id: SALE_ID,
  created_at: SALE.created_at,
  result: {
    command_type: "item.add",
    item_add: { items: [], requires_prescription_log: false },
  },
};

function legacyDraft(): string {
  const raw = JSON.stringify(DRAFT, null, 2);
  window.localStorage.setItem(LEGACY_DRAFT_KEY, raw);
  return raw;
}

function legacyCommand(): string {
  const command: PendingPosCommand = {
    version: 1,
    commandType: "item.add",
    operationId: OPERATION_ID,
    registerId: REGISTER_ID,
    createdAt: Date.now(),
    saleId: SALE_ID,
    catalogId: "catalog-1",
    qty: "1",
    expiredSaleConfirmed: false,
  };
  const raw = JSON.stringify(command, null, 2);
  window.localStorage.setItem(LEGACY_COMMAND_KEY, raw);
  return raw;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

function expectNoReplay(): void {
  expect(apiMocks.createSale).not.toHaveBeenCalled();
  expect(apiMocks.addSaleItem).not.toHaveBeenCalled();
  expect(apiMocks.updateSaleItem).not.toHaveBeenCalled();
  expect(apiMocks.deleteSaleItem).not.toHaveBeenCalled();
}

describe("preparePosRecovery", () => {
  beforeEach(() => {
    window.localStorage.clear();
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
    apiMocks.getSale.mockResolvedValue(SALE);
    apiMocks.getPosCommandResult.mockResolvedValue(COMMAND_RESULT);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("moves the owner's electronic draft verbatim without refreshing savedAt or losing markers", async () => {
    const raw = legacyDraft();
    expect(createPaymentAttemptOperation(SALE_ID, "card", "10.00")).not.toBeNull();
    expect(markPendingCompletion(SALE_ID)).toBe(true);
    const attemptKey = `pos:pendingPaymentAttempt:${SALE_ID}`;
    const completionKey = `pos:pendingCompletion:${SALE_ID}`;
    const attemptBefore = window.localStorage.getItem(attemptKey);
    const completionBefore = window.localStorage.getItem(completionKey);

    expect(await preparePosRecovery(REGISTER_ID, SCOPE, () => true)).toBe(true);

    expect(apiMocks.getSale).toHaveBeenCalledWith(SALE_ID);
    expect(window.localStorage.getItem(draftKey(REGISTER_ID, SCOPE))).toBe(raw);
    expect(window.localStorage.getItem(LEGACY_DRAFT_KEY)).toBeNull();
    expect(window.localStorage.getItem(attemptKey)).toBe(attemptBefore);
    expect(window.localStorage.getItem(completionKey)).toBe(completionBefore);
    expectNoReplay();
  });

  it.each([
    ["cashier even when the manager may read it", { cashier_user_id: "another-cashier" }, true],
    ["tenant", { tenant_id: "another-tenant" }, false],
    ["register", { register_id: "another-register" }, false],
    ["sale id", { id: "another-sale" }, false],
  ] as const)(
    "does not adopt or delete a legacy draft for another %s",
    async (_label, mismatch, canProceed) => {
      const raw = legacyDraft();
      apiMocks.getSale.mockResolvedValue({ ...SALE, ...mismatch });

      expect(await preparePosRecovery(REGISTER_ID, SCOPE, () => true)).toBe(canProceed);

      expect(window.localStorage.getItem(LEGACY_DRAFT_KEY)).toBe(raw);
      expect(window.localStorage.getItem(draftKey(REGISTER_ID, SCOPE))).toBeNull();
      expectNoReplay();
    },
  );

  it("does not write after the active user changes while ownership is being checked", async () => {
    const raw = legacyDraft();
    const saleRequest = deferred<SaleDetails>();
    apiMocks.getSale.mockReturnValue(saleRequest.promise);
    let active = true;
    const recovery = preparePosRecovery(REGISTER_ID, SCOPE, () => active);

    active = false;
    saleRequest.resolve(SALE);

    expect(await recovery).toBe(false);
    expect(window.localStorage.getItem(LEGACY_DRAFT_KEY)).toBe(raw);
    expect(window.localStorage.getItem(draftKey(REGISTER_ID, SCOPE))).toBeNull();
  });

  it("moves a confirmed command verbatim only after checking the sale owner", async () => {
    const raw = legacyCommand();

    expect(await preparePosRecovery(REGISTER_ID, SCOPE, () => true)).toBe(true);

    expect(apiMocks.getPosCommandResult).toHaveBeenCalledWith(OPERATION_ID);
    expect(apiMocks.getSale).toHaveBeenCalledWith(SALE_ID);
    expect(window.localStorage.getItem(pendingPosCommandKey(REGISTER_ID, SCOPE))).toBe(raw);
    expect(window.localStorage.getItem(LEGACY_COMMAND_KEY)).toBeNull();
    expectNoReplay();
  });

  it("blocks a legacy command with an unknown result without replaying or writing", async () => {
    const raw = legacyCommand();
    apiMocks.getPosCommandResult.mockRejectedValue({
      isAxiosError: true,
      response: { status: 404 },
    });
    const write = vi.spyOn(Storage.prototype, "setItem");

    expect(await preparePosRecovery(REGISTER_ID, SCOPE, () => true)).toBe(false);

    expect(write).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(LEGACY_COMMAND_KEY)).toBe(raw);
    expect(window.localStorage.getItem(pendingPosCommandKey(REGISTER_ID, SCOPE))).toBeNull();
    expectNoReplay();
  });

  it.each([
    ["cashier", { cashier_user_id: "another-cashier" }],
    ["tenant", { tenant_id: "another-tenant" }],
    ["register", { register_id: "another-register" }],
    ["sale id", { id: "another-sale" }],
  ] as const)("blocks a legacy command when its sale has a different %s", async (_label, mismatch) => {
    const raw = legacyCommand();
    apiMocks.getSale.mockResolvedValue({ ...SALE, ...mismatch });

    expect(await preparePosRecovery(REGISTER_ID, SCOPE, () => true)).toBe(false);

    expect(window.localStorage.getItem(LEGACY_COMMAND_KEY)).toBe(raw);
    expect(window.localStorage.getItem(pendingPosCommandKey(REGISTER_ID, SCOPE))).toBeNull();
    expectNoReplay();
  });

  it.each(["draft", "command"] as const)(
    "keeps the legacy %s if durable storage rejects the migration",
    async (kind) => {
      const raw = kind === "draft" ? legacyDraft() : legacyCommand();
      const legacyKey = kind === "draft" ? LEGACY_DRAFT_KEY : LEGACY_COMMAND_KEY;
      const scopedKey =
        kind === "draft"
          ? draftKey(REGISTER_ID, SCOPE)
          : pendingPosCommandKey(REGISTER_ID, SCOPE);
      vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
        throw new DOMException("Storage quota exceeded", "QuotaExceededError");
      });

      expect(await preparePosRecovery(REGISTER_ID, SCOPE, () => true)).toBe(false);

      expect(window.localStorage.getItem(legacyKey)).toBe(raw);
      expect(window.localStorage.getItem(scopedKey)).toBeNull();
    },
  );

  it.each(["draft", "command"] as const)(
    "preserves both versions when the scoped %s already exists",
    async (kind) => {
      const raw = kind === "draft" ? legacyDraft() : legacyCommand();
      const legacyKey = kind === "draft" ? LEGACY_DRAFT_KEY : LEGACY_COMMAND_KEY;
      const scopedKey =
        kind === "draft"
          ? draftKey(REGISTER_ID, SCOPE)
          : pendingPosCommandKey(REGISTER_ID, SCOPE);
      const existing =
        kind === "draft"
          ? raw.replace(SALE_ID, "10000000-0000-4000-8000-000000000002")
          : raw.replace(OPERATION_ID, "20000000-0000-4000-8000-000000000002");
      window.localStorage.setItem(scopedKey, existing);

      expect(await preparePosRecovery(REGISTER_ID, SCOPE, () => true)).toBe(false);

      expect(window.localStorage.getItem(scopedKey)).toBe(existing);
      expect(window.localStorage.getItem(legacyKey)).toBe(raw);
    },
  );

  it.each(["draft", "command"] as const)(
    "does not overwrite a legacy %s replaced while the server responds",
    async (kind) => {
      const raw = kind === "draft" ? legacyDraft() : legacyCommand();
      const legacyKey = kind === "draft" ? LEGACY_DRAFT_KEY : LEGACY_COMMAND_KEY;
      const scopedKey =
        kind === "draft"
          ? draftKey(REGISTER_ID, SCOPE)
          : pendingPosCommandKey(REGISTER_ID, SCOPE);
      const saleRequest = deferred<SaleDetails>();
      apiMocks.getSale.mockReturnValue(saleRequest.promise);
      const recovery = preparePosRecovery(REGISTER_ID, SCOPE, () => true);
      const replacement = `${raw}\n`;
      window.localStorage.setItem(legacyKey, replacement);
      saleRequest.resolve(SALE);

      expect(await recovery).toBe(false);
      expect(window.localStorage.getItem(legacyKey)).toBe(replacement);
      expect(window.localStorage.getItem(scopedKey)).toBeNull();
    },
  );
});
