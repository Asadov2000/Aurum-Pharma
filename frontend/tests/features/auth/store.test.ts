import { afterEach, describe, expect, it, vi } from "vitest";

import { type MeResponse } from "@/features/auth/types";
import { queryClient } from "@/lib/query";
import { getAuthSessionGeneration, useAuthStore } from "@/stores/auth";

const cashier: MeResponse = {
  id: "cashier",
  email: "synthetic@example.test",
  full_name: "Кассир",
  is_developer: false,
  is_administrator: false,
  home_tenant_id: "tenant",
  active_tenant_id: "tenant",
  status: "active",
  last_login_at: null,
  level: 4,
  is_tenant_owner: false,
  branch_assignments: { A: "role", B: "role" },
  permissions: ["batches.view", "pos.sell"],
  permission_scopes: { "batches.view": ["A", "B"], "pos.sell": ["A", "B"] },
  platform_capabilities: [],
  support_access: null,
};

describe("useAuthStore", () => {
  afterEach(() => {
    queryClient.clear();
    useAuthStore.getState().clear();
    window.localStorage.clear();
  });

  it("removes old branch data on revocation while retaining identity and login", () => {
    useAuthStore.getState().setUser(cashier);
    useAuthStore
      .getState()
      .setTokens({ access_token: "access", token_type: "bearer", expires_in: 900 });
    const generation = getAuthSessionGeneration();
    const narrowed = {
      ...cashier,
      branch_assignments: { A: "role" },
      permission_scopes: { "batches.view": ["A"], "pos.sell": ["A"] },
    };
    queryClient.setQueryData(["auth", "me"], narrowed);
    queryClient.setQueryData(["inventory", "batches"], [{ branch_id: "B" }]);
    useAuthStore.getState().setUser(narrowed);
    expect(getAuthSessionGeneration()).toBeGreaterThan(generation);
    expect(queryClient.getQueryData(["inventory", "batches"])).toBeUndefined();
    expect(queryClient.getQueryData(["auth", "me"])).toEqual(narrowed);
    expect(useAuthStore.getState().accessToken).toBe("access");
    expect(useAuthStore.getState().user).toEqual(narrowed);
  });

  it("cancels an old-scope read before its late completion can refill the cache", async () => {
    useAuthStore.getState().setUser(cashier);
    let finish!: (value: unknown[]) => void;
    let signal!: AbortSignal;
    const pending = queryClient.fetchQuery({
      queryKey: ["inventory", "batches"],
      queryFn: (context) => {
        signal = context.signal;
        return new Promise<unknown[]>((resolve) => {
          finish = resolve;
        });
      },
    });
    const cancelled = expect(pending).rejects.toThrow();
    useAuthStore.getState().setUser({ ...cashier, permission_scopes: { "batches.view": ["A"] } });
    expect(signal.aborted).toBe(true);
    finish([{ branch_id: "B" }]);
    await cancelled;
    expect(queryClient.getQueryData(["inventory", "batches"])).toBeUndefined();
  });

  it.each([
    ["permissions", { permissions: ["batches.view"] }],
    ["tenant-wide scope", { permission_scopes: { "batches.view": null, "pos.sell": null } }],
    ["assignment", { branch_assignments: { A: "role" } }],
    ["owner access", { is_tenant_owner: true }],
    ["status", { status: "inactive" }],
  ] satisfies Array<[string, Partial<MeResponse>]>)(
    "invalidates previous authority on %s change",
    (_name, change) => {
      useAuthStore.getState().setUser(cashier);
      const generation = getAuthSessionGeneration();
      queryClient.setQueryData(["sales"], [{ branch_id: "B" }]);
      useAuthStore.getState().setUser({ ...cashier, ...change });
      expect(getAuthSessionGeneration()).toBeGreaterThan(generation);
      expect(queryClient.getQueryData(["sales"])).toBeUndefined();
    },
  );

  it("invalidates a changed home tenant when the active tenant is absent", () => {
    useAuthStore.getState().setUser({ ...cashier, active_tenant_id: null });
    const generation = getAuthSessionGeneration();
    queryClient.setQueryData(["inventory"], [{ branch_id: "B" }]);
    useAuthStore
      .getState()
      .setUser({ ...cashier, active_tenant_id: null, home_tenant_id: "other-tenant" });
    expect(getAuthSessionGeneration()).toBeGreaterThan(generation);
    expect(queryClient.getQueryData(["inventory"])).toBeUndefined();
  });

  it("retains data during profile edits, reordered grants, token rotation and offline events", () => {
    useAuthStore.getState().setUser(cashier);
    const generation = getAuthSessionGeneration();
    queryClient.setQueryData(["inventory"], [{ branch_id: "A" }]);
    const listener = vi.fn();
    window.addEventListener("aurum:client-session-cleared", listener);
    try {
      useAuthStore.getState().setUser({
        ...cashier,
        full_name: "Новое имя",
        permissions: ["pos.sell", "batches.view"],
        branch_assignments: { B: "role", A: "role" },
        permission_scopes: { "pos.sell": ["B", "A"], "batches.view": ["B", "A"] },
      });
      useAuthStore
        .getState()
        .setTokens({ access_token: "rotated", token_type: "bearer", expires_in: 900 });
      window.dispatchEvent(new Event("offline"));
      window.dispatchEvent(new Event("online"));
      expect(getAuthSessionGeneration()).toBe(generation);
      expect(queryClient.getQueryData(["inventory"])).toEqual([{ branch_id: "A" }]);
      expect(listener).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener("aurum:client-session-cleared", listener);
    }
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
