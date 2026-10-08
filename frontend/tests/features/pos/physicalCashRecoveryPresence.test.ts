import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { draftKey, loadDraft } from "@/features/pos/draftStorage";
import {
  hasPhysicalCashRecovery,
  physicalCashRecoveryKey,
} from "@/features/pos/physicalCashRecoveryPresence";

import { authenticatePos, POS_SCOPE } from "./posIdentityFixture";

const saleId = "sale-cash";
const registerId = "register-cash";
const key = physicalCashRecoveryKey(saleId, POS_SCOPE);

function saveOldDraft(scope = POS_SCOPE): string {
  const raw = JSON.stringify({
    saleId,
    nameById: {},
    savedAt: Date.now() - 31 * 24 * 60 * 60_000,
  });
  localStorage.setItem(draftKey(registerId, scope), raw);
  return raw;
}

describe("physical cash bootstrap and draft retention", () => {
  beforeEach(() => {
    authenticatePos();
    localStorage.clear();
  });
  afterEach(() => vi.restoreAllMocks());

  it("returns false only when the scoped payment marker is absent", () => {
    expect(hasPhysicalCashRecovery(saleId, POS_SCOPE)).toBe(false);
  });

  it.each([
    '{"version":1,"createdAt":1,"request":{"operation_id":"fixed-operation"}}',
    "{broken payment",
    "null",
    "",
  ])("retains a 31-day draft and the exact financial bytes: %#", (raw) => {
    const draft = saveOldDraft();
    localStorage.setItem(key, raw);

    expect(hasPhysicalCashRecovery(saleId, POS_SCOPE)).toBe(true);
    expect(loadDraft(registerId, 30, POS_SCOPE)).toMatchObject({ saleId, expired: false });
    expect(localStorage.getItem(key)).toBe(raw);
    expect(localStorage.getItem(draftKey(registerId, POS_SCOPE))).toBe(draft);
  });

  it("retains the draft when the financial marker cannot be read", () => {
    const draft = saveOldDraft();
    const read = Storage.prototype.getItem;
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(function (this: Storage, name) {
      if (name === key) throw new DOMException("Storage unavailable", "SecurityError");
      return read.call(this, name);
    });

    expect(hasPhysicalCashRecovery(saleId, POS_SCOPE)).toBe(true);
    expect(loadDraft(registerId, 30, POS_SCOPE)).toMatchObject({ saleId, expired: false });
    expect(localStorage.getItem(draftKey(registerId, POS_SCOPE))).toBe(draft);
  });

  it.each([
    { tenantId: "another-tenant", userId: POS_SCOPE.userId },
    { tenantId: POS_SCOPE.tenantId, userId: "another-cashier" },
  ])("does not restore another owner's draft because of this owner's marker: %j", (scope) => {
    saveOldDraft(scope);
    const originalDraft = saveOldDraft();
    localStorage.setItem(key, "{uncertain payment");

    expect(hasPhysicalCashRecovery(saleId, scope)).toBe(false);
    expect(loadDraft(registerId, 30, scope)).toMatchObject({ saleId: null, expired: true });
    expect(localStorage.getItem(key)).toBe("{uncertain payment");
    expect(localStorage.getItem(draftKey(registerId, POS_SCOPE))).toBe(originalDraft);
  });

  it("expires an ordinary 31-day draft once no payment marker remains", () => {
    saveOldDraft();
    expect(loadDraft(registerId, 30, POS_SCOPE)).toMatchObject({ saleId: null, expired: true });
    expect(localStorage.getItem(draftKey(registerId, POS_SCOPE))).toBeNull();
  });
});
