import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearPendingCompletion,
  hasPendingCompletion,
  markPendingCompletion,
} from "@/features/pos/completionOperation";
import { draftKey, loadDraft } from "@/features/pos/draftStorage";

describe("pending POS completion", () => {
  beforeEach(() => window.localStorage.clear());

  it("persists and clears the completion marker", () => {
    expect(markPendingCompletion("sale-1")).toBe(true);
    expect(hasPendingCompletion("sale-1")).toBe(true);

    clearPendingCompletion("sale-1");
    expect(hasPendingCompletion("sale-1")).toBe(false);
  });

  it("reports a storage failure before completion is sent", () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage unavailable");
    });

    expect(markPendingCompletion("sale-1")).toBe(false);
    setItem.mockRestore();
  });

  it("keeps an expired draft while completion is unresolved", () => {
    window.localStorage.setItem(
      draftKey("register-1"),
      JSON.stringify({ saleId: "sale-1", nameById: {}, savedAt: 0 }),
    );
    expect(markPendingCompletion("sale-1")).toBe(true);

    expect(loadDraft("register-1", 1)).toEqual({
      saleId: "sale-1",
      nameById: {},
      expired: false,
      requiresRx: false,
      stagedPayments: [],
    });

    clearPendingCompletion("sale-1");
    expect(loadDraft("register-1", 1)).toEqual({
      saleId: null,
      nameById: {},
      expired: true,
      requiresRx: false,
      stagedPayments: [],
    });
  });

  it("keeps a completed receipt pointer after the draft TTL", () => {
    window.localStorage.setItem(
      draftKey("register-1"),
      JSON.stringify({
        saleId: "sale-1",
        nameById: {},
        savedAt: 0,
        status: "completed",
      }),
    );

    expect(loadDraft("register-1", 1)).toEqual({
      saleId: "sale-1",
      nameById: {},
      expired: false,
      requiresRx: false,
      stagedPayments: [],
    });
  });
});
