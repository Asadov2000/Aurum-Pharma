import { afterEach, describe, expect, it } from "vitest";

import { clearTokens, loadTokens, saveTokens } from "@/features/auth/storage";

describe("auth/storage", () => {
  afterEach(() => {
    window.localStorage.clear();
  });

  it("does not persist tokens and removes legacy auth keys", () => {
    window.localStorage.setItem("aurum.access_token", "legacy-access");
    window.localStorage.setItem("aurum.refresh_token", "legacy-refresh");
    saveTokens({
      access_token: "a.b.c",
      token_type: "bearer",
      expires_in: 900,
    });
    expect(loadTokens()).toEqual({ access: null });
    expect(window.localStorage.getItem("aurum.access_token")).toBeNull();
    expect(window.localStorage.getItem("aurum.refresh_token")).toBeNull();
  });

  it("clearTokens wipes everything", () => {
    window.localStorage.setItem("aurum.access_token", "legacy-access");
    window.localStorage.setItem("aurum.refresh_token", "legacy-refresh");
    saveTokens({
      access_token: "x",
      token_type: "bearer",
      expires_in: 1,
    });
    clearTokens();
    expect(loadTokens()).toEqual({ access: null });
    expect(window.localStorage.getItem("aurum.access_token")).toBeNull();
    expect(window.localStorage.getItem("aurum.refresh_token")).toBeNull();
  });

  it("loadTokens returns nulls for an empty store", () => {
    expect(loadTokens()).toEqual({ access: null });
  });
});
