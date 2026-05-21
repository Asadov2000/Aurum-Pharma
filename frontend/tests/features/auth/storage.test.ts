import { afterEach, describe, expect, it } from "vitest";

import { clearTokens, loadTokens, saveTokens } from "@/features/auth/storage";

describe("auth/storage", () => {
  afterEach(() => {
    window.localStorage.clear();
  });

  it("round-trips a token pair", () => {
    saveTokens({
      access_token: "a.b.c",
      refresh_token: "r.r.r",
      token_type: "bearer",
      expires_in: 900,
    });
    expect(loadTokens()).toEqual({ access: "a.b.c", refresh: "r.r.r" });
  });

  it("clearTokens wipes everything", () => {
    saveTokens({
      access_token: "x",
      refresh_token: "y",
      token_type: "bearer",
      expires_in: 1,
    });
    clearTokens();
    expect(loadTokens()).toEqual({ access: null, refresh: null });
  });

  it("loadTokens returns nulls for an empty store", () => {
    expect(loadTokens()).toEqual({ access: null, refresh: null });
  });
});
