import { authenticatePos, POS_SCOPE } from "./posIdentityFixture";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { DRAFT_TTL_MIN, draftKey, loadDraft, saveDraft } from "@/features/pos/draftStorage";
import { unitPaymentMarkerKey } from "@/features/pos/unitPaymentPreparationPresence";

const REG = "reg-1";
const ATTEMPT_ID = "30000000-0000-4000-8000-000000000001";

describe("loadDraft (POS draft TTL)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    authenticatePos();
  });
  afterEach(() => window.localStorage.clear());

  it("returns an empty draft when nothing is stored", () => {
    expect(loadDraft(REG)).toEqual({
      saleId: null,
      nameById: {},
      expired: false,
      requiresRx: false,
      stagedPayments: [],
      externalPaymentReviewRequired: false,
    });
  });

  it.each([ATTEMPT_ID, "corrupt-marker"])(
    "retains an expired draft while preparation recovery exists: %s",
    (marker) => {
      window.localStorage.setItem(
        draftKey(REG, POS_SCOPE),
        JSON.stringify({ saleId: "s-preparation", nameById: {}, savedAt: 0 }),
      );
      window.localStorage.setItem(unitPaymentMarkerKey("s-preparation", POS_SCOPE), marker);
      expect(loadDraft(REG, 1)).toMatchObject({ saleId: "s-preparation", expired: false });
      // A marker from another cashier cannot preserve this cashier's stale draft.
      window.localStorage.removeItem(unitPaymentMarkerKey("s-preparation", POS_SCOPE));
      window.localStorage.setItem(
        unitPaymentMarkerKey("s-preparation", { ...POS_SCOPE, userId: "another-cashier" }),
        marker,
      );
      expect(loadDraft(REG, 1)).toMatchObject({ saleId: null, expired: true });
    },
  );

  it("restores a fresh draft", () => {
    window.localStorage.setItem(
      draftKey(REG, POS_SCOPE),
      JSON.stringify({ saleId: "s-1", nameById: { c1: "Аспирин" }, savedAt: Date.now() }),
    );
    expect(loadDraft(REG)).toEqual({
      saleId: "s-1",
      nameById: { c1: "Аспирин" },
      expired: false,
      requiresRx: false,
      stagedPayments: [],
      externalPaymentReviewRequired: false,
    });
  });

  it("restores only the non-sensitive prescription requirement flag", () => {
    window.localStorage.setItem(
      draftKey(REG, POS_SCOPE),
      JSON.stringify({
        saleId: "s-rx",
        nameById: {},
        savedAt: Date.now(),
        requiresRx: true,
      }),
    );

    expect(loadDraft(REG)).toEqual({
      saleId: "s-rx",
      nameById: {},
      expired: false,
      requiresRx: true,
      stagedPayments: [],
      externalPaymentReviewRequired: false,
    });
  });

  it("uses the TTL passed from tenant settings (draft_sale_lifetime_min)", () => {
    // Saved 20 min ago: kept under a 30-min limit, expired under a 10-min one.
    const twentyMinAgo = Date.now() - 20 * 60_000;
    window.localStorage.setItem(
      draftKey(REG, POS_SCOPE),
      JSON.stringify({ saleId: "s-1", nameById: {}, savedAt: twentyMinAgo }),
    );
    expect(loadDraft(REG, 30).saleId).toBe("s-1");

    window.localStorage.setItem(
      draftKey(REG, POS_SCOPE),
      JSON.stringify({ saleId: "s-1", nameById: {}, savedAt: twentyMinAgo }),
    );
    const tight = loadDraft(REG, 10);
    expect(tight.saleId).toBeNull();
    expect(tight.expired).toBe(true);
  });

  it("drops and flags a draft older than the TTL, clearing storage", () => {
    const stale = Date.now() - (DRAFT_TTL_MIN + 1) * 60_000;
    window.localStorage.setItem(
      draftKey(REG, POS_SCOPE),
      JSON.stringify({ saleId: "s-old", nameById: {}, savedAt: stale }),
    );
    const r = loadDraft(REG, DRAFT_TTL_MIN);
    expect(r.saleId).toBeNull();
    expect(r.expired).toBe(true);
    // Storage must be wiped so it can't resurrect on the next load.
    expect(window.localStorage.getItem(draftKey(REG, POS_SCOPE))).toBeNull();
  });

  it("falls back to the default TTL when none is passed", () => {
    const stale = Date.now() - (DRAFT_TTL_MIN + 1) * 60_000;
    window.localStorage.setItem(
      draftKey(REG, POS_SCOPE),
      JSON.stringify({ saleId: "s-old", nameById: {}, savedAt: stale }),
    );
    expect(loadDraft(REG).expired).toBe(true);
  });

  it("treats a legacy draft without savedAt as expired (savedAt=0)", () => {
    window.localStorage.setItem(
      draftKey(REG, POS_SCOPE),
      JSON.stringify({ saleId: "s-x", nameById: {} }),
    );
    const r = loadDraft(REG, 30);
    expect(r.saleId).toBeNull();
    expect(r.expired).toBe(true);
  });

  it("ignores corrupt JSON", () => {
    window.localStorage.setItem(draftKey(REG, POS_SCOPE), "{not json");
    expect(loadDraft(REG, 30)).toEqual({
      saleId: null,
      nameById: {},
      expired: false,
      requiresRx: false,
      stagedPayments: [],
      externalPaymentReviewRequired: false,
    });
  });

  it("restores a validated mixed-payment calculation including cash tender", () => {
    expect(
      saveDraft(REG, "s-pay", {}, "draft", false, [
        {
          payment_method: "cash",
          amount: "20.00",
          metadata: { cash_received: "50.00" },
        },
        {
          payment_method: "card",
          amount: "30.00",
          payment_attempt_id: ATTEMPT_ID,
        },
      ]),
    ).toBe(true);

    expect(loadDraft(REG).stagedPayments).toEqual([
      {
        payment_method: "cash",
        amount: "20.00",
        metadata: { cash_received: "50.00" },
      },
      {
        payment_method: "card",
        amount: "30.00",
        payment_attempt_id: ATTEMPT_ID,
      },
    ]);
  });

  it("restores the external-payment review lock", () => {
    expect(saveDraft(REG, "s-review", {}, "draft", false, [], false, true)).toBe(true);
    expect(loadDraft(REG).externalPaymentReviewRequired).toBe(true);
  });

  it("never expires a draft with a confirmed electronic payment", () => {
    window.localStorage.setItem(
      draftKey(REG, POS_SCOPE),
      JSON.stringify({
        saleId: "s-confirmed",
        nameById: {},
        savedAt: 0,
        stagedPayments: [
          {
            payment_method: "card",
            amount: "30.00",
            payment_attempt_id: ATTEMPT_ID,
          },
        ],
      }),
    );

    expect(loadDraft(REG, 1)).toMatchObject({
      saleId: "s-confirmed",
      expired: false,
      stagedPayments: [{ payment_attempt_id: ATTEMPT_ID }],
    });
    expect(window.localStorage.getItem(draftKey(REG, POS_SCOPE))).not.toBeNull();
  });

  it("never expires a draft locked for external-payment review", () => {
    window.localStorage.setItem(
      draftKey(REG, POS_SCOPE),
      JSON.stringify({
        saleId: "s-review",
        nameById: {},
        savedAt: 0,
        externalPaymentReviewRequired: true,
      }),
    );

    expect(loadDraft(REG, 1)).toMatchObject({
      saleId: "s-review",
      expired: false,
      externalPaymentReviewRequired: true,
    });
  });

  it("restores an explicit expired-stock acknowledgement", () => {
    expect(saveDraft(REG, "s-expired", {}, "draft", false, [], true)).toBe(true);

    expect(loadDraft(REG).expiredSaleConfirmed).toBe(true);
  });

  it("drops an invalid staged calculation without dropping the cart", () => {
    window.localStorage.setItem(
      draftKey(REG, POS_SCOPE),
      JSON.stringify({
        saleId: "s-pay",
        nameById: {},
        savedAt: Date.now(),
        stagedPayments: [{ payment_method: "card", amount: "NaN" }],
      }),
    );

    const restored = loadDraft(REG);
    expect(restored.saleId).toBe("s-pay");
    expect(restored.stagedPayments).toEqual([]);
  });

  it("drops an unconfirmed card calculation without dropping the cart", () => {
    window.localStorage.setItem(
      draftKey(REG, POS_SCOPE),
      JSON.stringify({
        saleId: "s-pay",
        nameById: {},
        savedAt: Date.now(),
        stagedPayments: [{ payment_method: "card", amount: "30.00" }],
      }),
    );

    const restored = loadDraft(REG);
    expect(restored.saleId).toBe("s-pay");
    expect(restored.stagedPayments).toEqual([]);
  });
});
