import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const KEY = "aurum.auth_logout.v1";
const CONFIRMATION_KEY = `${KEY}.confirmation`;
const SIGN_IN_KEY = `${KEY}.signin`;
const OPERATION_ID = "5ae8e6ab-d0b8-4cc0-a4bb-a0904f6be154";
const OTHER_ID = "15486ad5-4371-4b95-94c0-e40920a06214";
const subscriptions: Array<() => void> = [];

function markerCookieName(key = KEY): string {
  const protocol = window.location.protocol === "https:" ? "https" : "http";
  const port = window.location.port || (protocol === "https" ? "443" : "80");
  return `${key.replaceAll(".", "_")}_${protocol}_${port}`;
}

function cleanupMarkers(): void {
  window.localStorage.clear();
  for (const part of document.cookie.split(";")) {
    const name = part.trim().split("=")[0];
    if (name?.startsWith("aurum_auth_logout_v1_")) {
      document.cookie = `${name}=; Path=/; Max-Age=0`;
    }
  }
}

async function loadState() {
  vi.resetModules();
  return import("@/features/auth/logoutState");
}

beforeEach(() => {
  cleanupMarkers();
  vi.stubGlobal("BroadcastChannel", undefined);
});

afterEach(() => {
  subscriptions.splice(0).forEach((unsubscribe) => unsubscribe());
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  cleanupMarkers();
});

describe("auth/logoutState", () => {
  it("closes access even when randomUUID is unavailable in the browser context", async () => {
    vi.stubGlobal("crypto", { getRandomValues: crypto.getRandomValues.bind(crypto) });
    const state = await loadState();
    const intent = state.beginLogout(null);
    expect(intent.id).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    expect(state.isSessionRestorationBlocked()).toBe(true);
  });

  it("allows reliable absence and probes persistence only once per page", async () => {
    const state = await loadState();
    const write = vi.spyOn(Storage.prototype, "setItem");
    expect(state.readLogoutState()).toBeNull();
    expect(write).toHaveBeenCalledTimes(1);
    expect(state.getAuthEpoch()).toBeNull();
    expect(state.isSessionRestorationBlocked()).toBe(false);
    expect(write).toHaveBeenCalledTimes(1);
    expect(window.localStorage.length).toBe(0);
    expect(document.cookie).toBe("");
  });

  it("preserves pending/confirmed logout across reload without overwriting intent", async () => {
    const state = await loadState();
    const intent = state.beginLogout(OPERATION_ID);
    const immutableIntent = window.localStorage.getItem(KEY);
    expect(state.readLogoutState()).toEqual(intent);
    expect(state.isSessionRestorationBlocked()).toBe(true);
    state.confirmLogout(intent.id);
    expect(window.localStorage.getItem(KEY)).toBe(immutableIntent);
    expect(state.readLogoutState()).toEqual({ ...intent, phase: "confirmed" });
    const reloaded = await loadState();
    expect(reloaded.readLogoutState()).toEqual({ ...intent, phase: "confirmed" });
    expect(reloaded.isSessionRestorationBlocked()).toBe(true);
  });

  it("records a new sign-in epoch acknowledging only the observed logout", async () => {
    const state = await loadState();
    const intent = state.beginLogout(null);
    state.confirmLogout(intent.id);
    expect(state.markExplicitLogin(intent.id)).toBe(true);
    const signedIn = state.readLogoutState();
    expect(signedIn).toMatchObject({ phase: "signed-in", operationId: null });
    expect(signedIn?.id).not.toBe(intent.id);
    expect(JSON.parse(window.localStorage.getItem(SIGN_IN_KEY)!)).toEqual({
      id: signedIn?.id,
      acknowledgedLogoutId: intent.id,
    });
    expect(state.isSessionRestorationBlocked()).toBe(false);
    const reloaded = await loadState();
    expect(reloaded.getAuthEpoch()).toBe(signedIn?.id);
  });

  it("a newer logout dominates an old confirmation, including a concurrent write", async () => {
    const state = await loadState();
    const old = state.beginLogout(null);
    const originalSet = Storage.prototype.setItem;
    let newer: ReturnType<typeof state.beginLogout> | null = null;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (key, value) {
      if (key === CONFIRMATION_KEY) newer = state.beginLogout(OPERATION_ID);
      originalSet.call(this, key, value);
    });
    state.confirmLogout(old.id);
    expect(newer).not.toBeNull();
    expect(state.readLogoutState()).toEqual(newer);
    state.confirmLogout(old.id);
    expect(state.readLogoutState()?.phase).toBe("pending");
  });

  it("a newer logout dominates late sign-in before and during persistence", async () => {
    const state = await loadState();
    const old = state.beginLogout(null);
    const current = state.beginLogout(null);
    expect(state.markExplicitLogin(old.id)).toBe(false);
    const originalSet = Storage.prototype.setItem;
    let newer: ReturnType<typeof state.beginLogout> | null = null;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (key, value) {
      if (key === SIGN_IN_KEY) newer = state.beginLogout(OPERATION_ID);
      originalSet.call(this, key, value);
    });
    expect(state.markExplicitLogin(current.id)).toBe(false);
    expect(state.readLogoutState()).toEqual(newer);
    expect(JSON.parse(window.localStorage.getItem(SIGN_IN_KEY)!)).toMatchObject({
      acknowledgedLogoutId: current.id,
    });
  });

  it("never restores or rewrites malformed and discrepant records", async () => {
    for (const [key, value] of [
      [KEY, "not-json"],
      [KEY, JSON.stringify({ id: OTHER_ID, phase: "signed-in", operationId: null })],
      [KEY, JSON.stringify({ id: OTHER_ID, phase: "pending", operationId: "secret" })],
      [CONFIRMATION_KEY, JSON.stringify({ confirmedLogoutId: OTHER_ID, extra: true })],
      [SIGN_IN_KEY, JSON.stringify({ id: OTHER_ID, acknowledgedLogoutId: 1 })],
    ]) {
      cleanupMarkers();
      const state = await loadState();
      window.localStorage.setItem(key!, value!);
      expect(state.readLogoutState()).not.toBeNull();
      expect(state.getAuthEpoch()).toBe("blocked");
      expect(state.isSessionRestorationBlocked()).toBe(true);
      expect(state.markExplicitLogin("blocked")).toBe(false);
      expect(window.localStorage.getItem(key!)).toBe(value);
    }
    cleanupMarkers();
    const state = await loadState();
    state.beginLogout(null);
    document.cookie = `${markerCookieName()}=${encodeURIComponent(
      JSON.stringify({
        id: OTHER_ID,
        phase: "pending",
        operationId: null,
      }),
    )}; Path=/`;
    expect(state.getAuthEpoch()).toBe("blocked");
  });

  it("fails closed for storage reads/writes/removals and silently rejected cookies", async () => {
    for (const method of ["getItem", "setItem", "removeItem"] as const) {
      cleanupMarkers();
      const state = await loadState();
      const failure = vi.spyOn(Storage.prototype, method).mockImplementation(() => {
        throw new DOMException("Unavailable", "SecurityError");
      });
      expect(state.getAuthEpoch()).toBe("blocked");
      expect(state.isSessionRestorationBlocked()).toBe(true);
      failure.mockRestore();
    }
    cleanupMarkers();
    const state = await loadState();
    vi.spyOn(document, "cookie", "set").mockImplementation(() => undefined);
    expect(state.getAuthEpoch()).toBe("blocked");
    expect(state.isSessionRestorationBlocked()).toBe(true);
  });

  it("cookie fallback preserves logout intent after failed storage and reload", async () => {
    const state = await loadState();
    const failure = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("Unavailable", "QuotaExceededError");
    });
    const intent = state.beginLogout(OPERATION_ID);
    expect(state.getAuthEpoch()).toBe("blocked");
    expect(document.cookie).toContain(markerCookieName());
    failure.mockRestore();
    const reloaded = await loadState();
    expect(reloaded.readLogoutState()).toEqual(intent);
    expect(reloaded.isSessionRestorationBlocked()).toBe(true);
    expect(reloaded.markExplicitLogin(OTHER_ID)).toBe(false);
  });

  it("notifies same-tab, storage, broadcast, focus and pageshow; unsubscribes cleanly", async () => {
    class TestChannel extends EventTarget {
      static instance: TestChannel;
      postMessage = vi.fn();
      close = vi.fn();
      constructor() {
        super();
        TestChannel.instance = this;
      }
    }
    vi.stubGlobal("BroadcastChannel", TestChannel);
    const state = await loadState();
    const listener = vi.fn();
    const unsubscribe = state.subscribeLogoutState(listener);
    subscriptions.push(unsubscribe);
    state.beginLogout(null);
    expect(listener).toHaveBeenCalledTimes(1);
    expect(TestChannel.instance.postMessage).toHaveBeenCalledWith("changed");
    for (const key of [KEY, CONFIRMATION_KEY, SIGN_IN_KEY, null]) {
      window.dispatchEvent(new StorageEvent("storage", { key }));
    }
    window.dispatchEvent(new StorageEvent("storage", { key: "unrelated" }));
    TestChannel.instance.dispatchEvent(new MessageEvent("message", { data: "changed" }));
    window.dispatchEvent(new Event("focus"));
    window.dispatchEvent(new Event("pageshow"));
    expect(listener).toHaveBeenCalledTimes(8);
    unsubscribe();
    expect(TestChannel.instance.close).toHaveBeenCalledOnce();
    window.dispatchEvent(new Event("focus"));
    expect(listener).toHaveBeenCalledTimes(8);
  });

  it("uses protocol/port-scoped noncredential cookies and filters invalid operation IDs", async () => {
    const state = await loadState();
    const intent = state.beginLogout("not-a-refresh-operation");
    expect(intent.operationId).toBeNull();
    expect(document.cookie).toContain(`${markerCookieName()}=`);
    expect(JSON.parse(window.localStorage.getItem(KEY)!)).toEqual(intent);
    expect(Object.keys(intent).sort()).toEqual(["id", "operationId", "phase"]);
    state.confirmLogout(intent.id);
    expect(state.markExplicitLogin(intent.id)).toBe(true);
    for (let index = 0; index < window.localStorage.length; index++) {
      expect(window.localStorage.key(index)).toMatch(/^aurum\.auth_logout\.v1/);
    }
  });
});
