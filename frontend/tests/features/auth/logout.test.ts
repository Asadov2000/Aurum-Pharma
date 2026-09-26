import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  epoch: null as string | null,
  phase: "" as string,
  post: vi.fn(),
  clear: vi.fn(),
  confirms: vi.fn(),
  logins: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: { post: state.post },
  withoutAuth: (config: unknown) => config,
}));
vi.mock("@/features/auth/session", () => ({ clearClientSession: state.clear }));
vi.mock("@/features/auth/logoutState", () => ({
  getAuthEpoch: () => state.epoch,
  isSessionRestorationBlocked: () => ["pending", "confirmed"].includes(state.phase),
  readLogoutState: () =>
    state.epoch ? { id: state.epoch, phase: state.phase, operationId: null } : null,
  beginLogout: () => {
    state.epoch = crypto.randomUUID();
    state.phase = "pending";
  },
  confirmLogout: (id: string) => {
    state.confirms(id);
    if (state.epoch === id) state.phase = "confirmed";
  },
  markExplicitLogin: (epoch: string | null, recordEpoch?: (epoch: string) => void) => {
    state.logins(epoch);
    if (state.epoch !== epoch) return false;
    state.epoch = crypto.randomUUID();
    state.phase = "signed-in";
    recordEpoch?.(state.epoch);
    return true;
  },
  subscribeLogoutState: () => () => undefined,
}));

import {
  completePendingLogout,
  runCookieRequest,
  runSessionRequest,
} from "@/features/auth/cookieRequests";
import { logoutLocally } from "@/features/auth/logout";
import { useAuthStore } from "@/stores/auth";

let tokens = { access_token: "synthetic-access", token_type: "bearer", expires_in: 900 };

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

describe("safe logout and cookie ordering", () => {
  beforeEach(() => {
    tokens = { access_token: "synthetic-access", token_type: "bearer", expires_in: 900 };
    state.epoch = null;
    state.phase = "";
    state.post.mockReset().mockResolvedValue({ data: {} });
    state.clear.mockReset().mockImplementation(() => useAuthStore.getState().clear());
    state.confirms.mockClear();
    state.logins.mockClear();
    useAuthStore.getState().clear();
    let queue: Promise<unknown> = Promise.resolve();
    Object.defineProperty(navigator, "locks", {
      configurable: true,
      value: {
        request: (_name: string, execute: () => Promise<unknown>) => {
          const task = queue.then(execute);
          queue = task.catch(() => undefined);
          return task;
        },
      },
    });
  });

  it("rejects an accepted token when another explicit login changes the epoch before it is stored", async () => {
    const oldTokens = await runCookieRequest("explicit", async () => tokens);
    const newTokens = await runCookieRequest("explicit", async () => ({
      ...tokens,
      access_token: "new-access",
    }));
    useAuthStore.getState().setTokens(newTokens);
    useAuthStore.getState().setTokens(oldTokens);
    expect(useAuthStore.getState().accessToken).toBe("new-access");
  });

  it("does not accept an access-only password proof after logout", async () => {
    const response = deferred<typeof tokens>();
    const pending = runSessionRequest(() => response.promise);
    const rejected = expect(pending).rejects.toMatchObject({ code: "ERR_CANCELED" });
    state.epoch = "new-exit";
    state.phase = "pending";
    response.resolve(tokens);
    await rejected;
  });

  it("closes local access immediately and deduplicates a hanging logout", async () => {
    useAuthStore.getState().setTokens(tokens);
    const response = deferred<unknown>();
    state.post.mockReturnValue(response.promise);
    const logout = logoutLocally();
    expect(useAuthStore.getState().accessToken).toBeNull();
    expect(state.phase).toBe("pending");
    expect(logoutLocally()).toBe(logout);
    await vi.waitFor(() => expect(state.post).toHaveBeenCalledTimes(1));
    response.reject(new Error("offline"));
    await logout;
    expect(state.phase).toBe("pending");
  });

  it("keeps the barrier after a confirmed logout and rejects cookie refresh", async () => {
    await logoutLocally();
    expect(state.phase).toBe("confirmed");
    const refresh = vi.fn().mockResolvedValue(tokens);
    await expect(runCookieRequest("refresh", refresh)).rejects.toMatchObject({
      code: "ERR_CANCELED",
    });
    expect(refresh).not.toHaveBeenCalled();
  });

  it("finishes old logout before explicit login under the same lock", async () => {
    state.epoch = "old-exit";
    state.phase = "pending";
    const order: string[] = [];
    state.post.mockImplementation(async () => {
      order.push("logout");
    });
    await runCookieRequest("explicit", async () => {
      order.push("login");
      return tokens;
    });
    expect(order).toEqual(["logout", "login"]);
    expect(state.phase).toBe("signed-in");
    await completePendingLogout();
    expect(state.post).toHaveBeenCalledTimes(1);
  });

  it("does not send login credentials when pending logout cannot be confirmed", async () => {
    state.epoch = "old-exit";
    state.phase = "pending";
    state.post.mockRejectedValue(new Error("offline"));
    const login = vi.fn();
    await expect(runCookieRequest("explicit", login)).rejects.toThrow("offline");
    expect(login).not.toHaveBeenCalled();
    expect(state.phase).toBe("pending");
  });

  it("does not create a server session when persistent state is unreadable", async () => {
    state.epoch = "blocked";
    state.phase = "pending";
    const login = vi.fn();
    await expect(runCookieRequest("explicit", login)).rejects.toThrow("сохранить");
    expect(login).not.toHaveBeenCalled();
  });

  it.each(["explicit", "account"] as const)(
    "rejects a late %s token after a newer logout",
    async (kind) => {
      const response = deferred<typeof tokens>();
      const execute = vi.fn().mockReturnValue(response.promise);
      const pending = runCookieRequest(kind, execute);
      const rejected = expect(pending).rejects.toMatchObject({ code: "ERR_CANCELED" });
      await vi.waitFor(() => expect(execute).toHaveBeenCalledTimes(1));
      state.epoch = "new-exit";
      state.phase = "pending";
      response.resolve(tokens);
      await rejected;
      expect(state.logins).not.toHaveBeenCalled();
      expect(state.epoch).toBe("new-exit");
    },
  );

  it("waits for a late refresh cookie before submitting server logout", async () => {
    const response = deferred<typeof tokens>();
    const refresh = vi.fn().mockReturnValue(response.promise);
    const pending = runCookieRequest("refresh", refresh);
    const rejected = expect(pending).rejects.toMatchObject({ code: "ERR_CANCELED" });
    await vi.waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
    const logout = logoutLocally();
    expect(state.post).not.toHaveBeenCalled();
    response.resolve(tokens);
    await rejected;
    await logout;
    expect(state.post).toHaveBeenCalledTimes(1);
    expect(state.phase).toBe("confirmed");
  });

  it("fails closed without origin-wide locks after logout", async () => {
    Object.defineProperty(navigator, "locks", { configurable: true, value: undefined });
    state.epoch = "old-exit";
    state.phase = "confirmed";
    const login = vi.fn();
    await expect(runCookieRequest("explicit", login)).rejects.toThrow("HTTPS");
    expect(login).not.toHaveBeenCalled();
  });
});
