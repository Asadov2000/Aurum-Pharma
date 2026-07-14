import { afterEach, describe, expect, it } from "vitest";

import {
  clearRefreshOperationId,
  getOrCreateRefreshOperationId,
  getPendingRefreshOperationId,
} from "@/features/auth/refreshOperation";

describe("auth/refreshOperation", () => {
  afterEach(() => {
    clearRefreshOperationId();
    window.sessionStorage.clear();
  });

  it("keeps one UUID for all retries of the pending operation", () => {
    const first = getOrCreateRefreshOperationId();
    const retry = getOrCreateRefreshOperationId();

    expect(retry).toBe(first);
    expect(first).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );
  });

  it("rejects a tampered stored value", () => {
    window.sessionStorage.setItem("aurum.refresh_operation_id", "not-a-uuid");

    expect(getPendingRefreshOperationId()).toBeNull();
    expect(window.sessionStorage.getItem("aurum.refresh_operation_id")).toBeNull();
  });

  it("removes the operation after completion", () => {
    getOrCreateRefreshOperationId();
    clearRefreshOperationId();

    expect(getPendingRefreshOperationId()).toBeNull();
  });
});
