import { afterEach, describe, expect, it } from "vitest";

import { useAuthStore } from "@/stores/auth";

describe("useAuthStore", () => {
  afterEach(() => {
    useAuthStore.getState().clear();
    window.localStorage.clear();
  });

  it("setTokens keeps access in memory and does not persist auth tokens", () => {
    window.localStorage.setItem("aurum.access_token", "legacy-access");
    window.localStorage.setItem("aurum.refresh_token", "legacy-refresh");
    useAuthStore.getState().setTokens({
      access_token: "A",
      token_type: "bearer",
      expires_in: 900,
    });
    const state = useAuthStore.getState();
    expect(state.accessToken).toBe("A");
    expect(state.hydrated).toBe(true);
    expect(window.localStorage.getItem("aurum.access_token")).toBeNull();
    expect(window.localStorage.getItem("aurum.refresh_token")).toBeNull();
  });

  it("clear() wipes tokens and marks hydrated", () => {
    window.localStorage.setItem("aurum.access_token", "legacy-access");
    window.localStorage.setItem("aurum.refresh_token", "legacy-refresh");
    useAuthStore.getState().setTokens({
      access_token: "A",
      token_type: "bearer",
      expires_in: 900,
    });
    useAuthStore.getState().clear();
    const state = useAuthStore.getState();
    expect(state.accessToken).toBeNull();
    expect(state.user).toBeNull();
    expect(state.hydrated).toBe(true);
    expect(window.localStorage.getItem("aurum.access_token")).toBeNull();
    expect(window.localStorage.getItem("aurum.refresh_token")).toBeNull();
  });

  it("setUser stores the snapshot and hydrates", () => {
    useAuthStore.getState().setUser({
      id: "u-1",
      email: "x@y.tj",
      full_name: "N",
      is_developer: false,
      is_administrator: false,
      home_tenant_id: null,
      status: "active",
      last_login_at: null,
      level: 4,
      is_tenant_owner: false,
      branch_assignments: {},
      permissions: [],
    });
    expect(useAuthStore.getState().user?.email).toBe("x@y.tj");
    expect(useAuthStore.getState().hydrated).toBe(true);
  });
});
