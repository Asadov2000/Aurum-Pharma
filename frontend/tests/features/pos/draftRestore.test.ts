import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { DRAFT_TTL_MIN, draftKey, loadDraft } from "@/features/pos/draftStorage";

const REG = "reg-1";

describe("loadDraft (POS draft TTL)", () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => window.localStorage.clear());

  it("returns an empty draft when nothing is stored", () => {
    expect(loadDraft(REG)).toEqual({
      saleId: null,
      nameById: {},
      expired: false,
      requiresRx: false,
    });
  });

  it("restores a fresh draft", () => {
    window.localStorage.setItem(
      draftKey(REG),
      JSON.stringify({ saleId: "s-1", nameById: { c1: "Аспирин" }, savedAt: Date.now() }),
    );
    expect(loadDraft(REG)).toEqual({
      saleId: "s-1",
      nameById: { c1: "Аспирин" },
      expired: false,
      requiresRx: false,
    });
  });

  it("restores only the non-sensitive prescription requirement flag", () => {
    window.localStorage.setItem(
      draftKey(REG),
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
    });
  });

  it("uses the TTL passed from tenant settings (draft_sale_lifetime_min)", () => {
    // Saved 20 min ago: kept under a 30-min limit, expired under a 10-min one.
    const twentyMinAgo = Date.now() - 20 * 60_000;
    window.localStorage.setItem(
      draftKey(REG),
      JSON.stringify({ saleId: "s-1", nameById: {}, savedAt: twentyMinAgo }),
    );
    expect(loadDraft(REG, 30).saleId).toBe("s-1");

    window.localStorage.setItem(
      draftKey(REG),
      JSON.stringify({ saleId: "s-1", nameById: {}, savedAt: twentyMinAgo }),
    );
    const tight = loadDraft(REG, 10);
    expect(tight.saleId).toBeNull();
    expect(tight.expired).toBe(true);
  });

  it("drops and flags a draft older than the TTL, clearing storage", () => {
    const stale = Date.now() - (DRAFT_TTL_MIN + 1) * 60_000;
    window.localStorage.setItem(
      draftKey(REG),
      JSON.stringify({ saleId: "s-old", nameById: {}, savedAt: stale }),
    );
    const r = loadDraft(REG, DRAFT_TTL_MIN);
    expect(r.saleId).toBeNull();
    expect(r.expired).toBe(true);
    // Storage must be wiped so it can't resurrect on the next load.
    expect(window.localStorage.getItem(draftKey(REG))).toBeNull();
  });

  it("falls back to the default TTL when none is passed", () => {
    const stale = Date.now() - (DRAFT_TTL_MIN + 1) * 60_000;
    window.localStorage.setItem(
      draftKey(REG),
      JSON.stringify({ saleId: "s-old", nameById: {}, savedAt: stale }),
    );
    expect(loadDraft(REG).expired).toBe(true);
  });

  it("treats a legacy draft without savedAt as expired (savedAt=0)", () => {
    window.localStorage.setItem(draftKey(REG), JSON.stringify({ saleId: "s-x", nameById: {} }));
    const r = loadDraft(REG, 30);
    expect(r.saleId).toBeNull();
    expect(r.expired).toBe(true);
  });

  it("ignores corrupt JSON", () => {
    window.localStorage.setItem(draftKey(REG), "{not json");
    expect(loadDraft(REG, 30)).toEqual({
      saleId: null,
      nameById: {},
      expired: false,
      requiresRx: false,
    });
  });
});
