import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { DRAFT_TTL_MIN, draftKey, loadDraft } from "@/features/pos/draftStorage";

const REG = "reg-1";

describe("loadDraft (POS draft TTL)", () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => window.localStorage.clear());

  it("returns an empty draft when nothing is stored", () => {
    expect(loadDraft(REG)).toEqual({ saleId: null, nameById: {}, expired: false });
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
    });
  });

  it("drops and flags a draft older than the TTL, clearing storage", () => {
    const stale = Date.now() - (DRAFT_TTL_MIN + 1) * 60_000;
    window.localStorage.setItem(
      draftKey(REG),
      JSON.stringify({ saleId: "s-old", nameById: {}, savedAt: stale }),
    );
    const r = loadDraft(REG);
    expect(r.saleId).toBeNull();
    expect(r.expired).toBe(true);
    // Storage must be wiped so it can't resurrect on the next load.
    expect(window.localStorage.getItem(draftKey(REG))).toBeNull();
  });

  it("treats a legacy draft without savedAt as expired (savedAt=0)", () => {
    window.localStorage.setItem(draftKey(REG), JSON.stringify({ saleId: "s-x", nameById: {} }));
    const r = loadDraft(REG);
    expect(r.saleId).toBeNull();
    expect(r.expired).toBe(true);
  });

  it("ignores corrupt JSON", () => {
    window.localStorage.setItem(draftKey(REG), "{not json");
    expect(loadDraft(REG)).toEqual({ saleId: null, nameById: {}, expired: false });
  });
});
