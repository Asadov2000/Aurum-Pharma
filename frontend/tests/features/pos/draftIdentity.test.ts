import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearPendingPosCommand,
  createPendingPosCommand,
  loadPendingPosCommand,
  pendingPosCommandKey,
} from "@/features/pos/commandOperation";
import {
  clearDraft,
  draftKey,
  loadDraft,
  saveDraft,
  type SavedStagedPayment,
} from "@/features/pos/draftStorage";
import {
  capturePosSessionGuard,
  currentPosStorageScope,
  type PosStorageScope,
} from "@/features/pos/posStorageScope";
import { useAuthStore } from "@/stores/auth";

import { authenticatePos, POS_SCOPE } from "./posIdentityFixture";

const REGISTER_ID = "register-1";
const SALE_ID = "10000000-0000-4000-8000-000000000001";
const ATTEMPT_ID = "20000000-0000-4000-8000-000000000001";
const OTHER_USER: PosStorageScope = { tenantId: POS_SCOPE.tenantId, userId: "u-2" };
const OTHER_TENANT: PosStorageScope = { tenantId: "t-2", userId: POS_SCOPE.userId };
const MIXED_PAYMENTS: SavedStagedPayment[] = [
  { payment_method: "cash", amount: "5.00", metadata: { cash_received: "10.00" } },
  { payment_method: "card", amount: "5.00", payment_attempt_id: ATTEMPT_ID },
];
const EMPTY_DRAFT = {
  saleId: null,
  nameById: {},
  expired: false,
  requiresRx: false,
  stagedPayments: [],
  externalPaymentReviewRequired: false,
};

describe("POS draft identity", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useAuthStore.getState().clear();
    authenticatePos();
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-26T10:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
    useAuthStore.getState().clear();
    window.localStorage.clear();
  });

  it.each([
    ["mixed electronic payment", MIXED_PAYMENTS, false],
    ["uncertain external payment", [], true],
  ] as const)(
    "restores the same %s draft after reload and logout/login by its owner",
    (_label, payments, reviewRequired) => {
      expect(
        saveDraft(
          REGISTER_ID,
          SALE_ID,
          { "catalog-1": "Аспирин" },
          "draft",
          true,
          payments,
          true,
          reviewRequired,
        ),
      ).toBe(true);
      const raw = window.localStorage.getItem(draftKey(REGISTER_ID, POS_SCOPE));
      const expected = {
        saleId: SALE_ID,
        nameById: { "catalog-1": "Аспирин" },
        expired: false,
        requiresRx: true,
        stagedPayments: payments,
        expiredSaleConfirmed: true,
        externalPaymentReviewRequired: reviewRequired,
      };

      expect(loadDraft(REGISTER_ID)).toEqual(expected);
      vi.setSystemTime(new Date("2026-09-27T10:00:00Z"));
      expect(loadDraft(REGISTER_ID)).toEqual(expected);
      useAuthStore.getState().clear();
      expect(loadDraft(REGISTER_ID)).toEqual(EMPTY_DRAFT);
      expect(window.localStorage.getItem(draftKey(REGISTER_ID, POS_SCOPE))).toBe(raw);

      authenticatePos();
      expect(loadDraft(REGISTER_ID)).toEqual(expected);
      expect(window.localStorage.getItem(draftKey(REGISTER_ID, POS_SCOPE))).toBe(raw);
    },
  );

  it.each([
    ["another user", OTHER_USER],
    ["another tenant", OTHER_TENANT],
  ] as const)("isolates drafts and clears for %s on the same register", (_label, nextScope) => {
    expect(saveDraft(REGISTER_ID, SALE_ID, {})).toBe(true);
    const ownerKey = draftKey(REGISTER_ID, POS_SCOPE);
    const ownerRaw = window.localStorage.getItem(ownerKey);

    authenticatePos(nextScope);
    expect(currentPosStorageScope()).toEqual(nextScope);
    expect(loadDraft(REGISTER_ID)).toEqual(EMPTY_DRAFT);
    expect(clearDraft(REGISTER_ID)).toBe(true);
    expect(window.localStorage.getItem(ownerKey)).toBe(ownerRaw);

    expect(saveDraft(REGISTER_ID, "other-sale", {})).toBe(true);
    expect(loadDraft(REGISTER_ID).saleId).toBe("other-sale");
    expect(clearDraft(REGISTER_ID)).toBe(true);
    expect(loadDraft(REGISTER_ID)).toEqual(EMPTY_DRAFT);
    expect(window.localStorage.getItem(ownerKey)).toBe(ownerRaw);

    authenticatePos();
    expect(loadDraft(REGISTER_ID).saleId).toBe(SALE_ID);
  });

  it("keeps a captured scope when explicit storage calls finish after a user switch", () => {
    authenticatePos(OTHER_USER);
    expect(
      saveDraft(REGISTER_ID, SALE_ID, {}, "draft", false, [], false, true, POS_SCOPE),
    ).toBe(true);
    expect(loadDraft(REGISTER_ID)).toEqual(EMPTY_DRAFT);
    expect(loadDraft(REGISTER_ID, undefined, POS_SCOPE).saleId).toBe(SALE_ID);

    expect(saveDraft(REGISTER_ID, "other-sale", {})).toBe(true);
    expect(clearDraft(REGISTER_ID, POS_SCOPE)).toBe(true);
    expect(loadDraft(REGISTER_ID).saleId).toBe("other-sale");
    expect(loadDraft(REGISTER_ID, undefined, POS_SCOPE)).toEqual(EMPTY_DRAFT);
  });

  it("fails closed without an authenticated user and preserves every existing draft", () => {
    expect(saveDraft(REGISTER_ID, SALE_ID, {})).toBe(true);
    const scopedKey = draftKey(REGISTER_ID, POS_SCOPE);
    const raw = window.localStorage.getItem(scopedKey);
    const legacyKey = `pos:currentSale:${REGISTER_ID}`;
    window.localStorage.setItem(legacyKey, JSON.stringify({ saleId: "legacy-sale" }));
    const legacyRaw = window.localStorage.getItem(legacyKey);

    useAuthStore.getState().clear();

    expect(currentPosStorageScope()).toBeNull();
    expect(loadDraft(REGISTER_ID)).toEqual(EMPTY_DRAFT);
    expect(saveDraft(REGISTER_ID, "unowned-sale", {})).toBe(false);
    expect(clearDraft(REGISTER_ID)).toBe(false);
    expect(window.localStorage.getItem(scopedKey)).toBe(raw);
    expect(window.localStorage.getItem(legacyKey)).toBe(legacyRaw);
    expect(window.localStorage.length).toBe(2);
  });

  it.each([
    ["another user", OTHER_USER],
    ["another tenant", OTHER_TENANT],
  ] as const)("keeps pending commands separate for %s", (_label, nextScope) => {
    const ownerCommand = createPendingPosCommand({
      commandType: "sale.create",
      registerId: REGISTER_ID,
    });
    expect(ownerCommand).not.toBeNull();
    if (!ownerCommand) throw new Error("Expected a durable owner command");
    const ownerKey = pendingPosCommandKey(REGISTER_ID, POS_SCOPE);
    const ownerRaw = window.localStorage.getItem(ownerKey);

    authenticatePos(nextScope);
    expect(loadPendingPosCommand(REGISTER_ID)).toBeNull();
    expect(clearPendingPosCommand(REGISTER_ID, ownerCommand.operationId)).toBe(false);
    const nextCommand = createPendingPosCommand({
      commandType: "sale.create",
      registerId: REGISTER_ID,
    });
    expect(nextCommand).not.toBeNull();
    if (!nextCommand) throw new Error("Expected a durable next-user command");
    expect(nextCommand.operationId).not.toBe(ownerCommand.operationId);
    expect(loadPendingPosCommand(REGISTER_ID)).toEqual(nextCommand);
    expect(clearPendingPosCommand(REGISTER_ID, nextCommand.operationId)).toBe(true);
    expect(window.localStorage.getItem(ownerKey)).toBe(ownerRaw);

    authenticatePos();
    expect(loadPendingPosCommand(REGISTER_ID)).toEqual(ownerCommand);
  });

  it("uses the explicit command scope and rejects unauthenticated default operations", () => {
    const command = createPendingPosCommand(
      { commandType: "sale.create", registerId: REGISTER_ID },
      POS_SCOPE,
    );
    expect(command).not.toBeNull();
    if (!command) throw new Error("Expected a durable command");

    useAuthStore.getState().clear();

    expect(loadPendingPosCommand(REGISTER_ID)).toBeNull();
    expect(
      createPendingPosCommand({ commandType: "sale.create", registerId: REGISTER_ID }),
    ).toBeNull();
    expect(clearPendingPosCommand(REGISTER_ID, command.operationId)).toBe(false);
    expect(loadPendingPosCommand(REGISTER_ID, POS_SCOPE)).toEqual(command);
    expect(clearPendingPosCommand(REGISTER_ID, command.operationId, POS_SCOPE)).toBe(true);
    expect(loadPendingPosCommand(REGISTER_ID, POS_SCOPE)).toBeNull();
  });

  it.each([
    ["user", OTHER_USER],
    ["tenant", OTHER_TENANT],
  ] as const)("invalidates a captured session guard after the %s switches", (_label, nextScope) => {
    const guard = capturePosSessionGuard(POS_SCOPE);
    expect(guard()).toBe(true);

    authenticatePos(nextScope);
    expect(guard()).toBe(false);
    authenticatePos();
    expect(guard()).toBe(false);
    expect(capturePosSessionGuard(POS_SCOPE)()).toBe(true);
  });

  it("invalidates an old session guard after logout/login by the same user", () => {
    const guard = capturePosSessionGuard(POS_SCOPE);
    expect(guard()).toBe(true);

    useAuthStore.getState().clear();
    expect(guard()).toBe(false);
    authenticatePos();

    expect(currentPosStorageScope()).toEqual(POS_SCOPE);
    expect(guard()).toBe(false);
    expect(capturePosSessionGuard(POS_SCOPE)()).toBe(true);
  });
});
