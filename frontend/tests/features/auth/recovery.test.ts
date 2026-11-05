import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

function cookieLocks() {
  let tail: Promise<unknown> = Promise.resolve();
  let requested = 0;
  const waiters = new Map<number, ReturnType<typeof deferred<void>>>();
  const events: string[] = [];
  function request<T>(name: string, execute: () => Promise<T>): Promise<T> {
    const number = ++requested;
    events.push(`queued:${number}:${name}`);
    waiters.get(number)?.resolve();
    const pending = tail.then(async () => {
      events.push(`acquired:${number}`);
      try {
        return await execute();
      } finally {
        events.push(`released:${number}`);
      }
    });
    tail = pending.catch(() => undefined);
    return pending;
  }
  function queued(number: number): Promise<void> {
    if (requested >= number) return Promise.resolve();
    const waiter = deferred<void>();
    waiters.set(number, waiter);
    return waiter.promise;
  }
  return { request, queued, events };
}

function response(config: InternalAxiosRequestConfig, data: unknown): AxiosResponse<unknown> {
  return { data, status: 200, statusText: "OK", headers: {}, config };
}

function rejection(
  config: InternalAxiosRequestConfig,
  status: number,
  reason?: string,
): AxiosError {
  return new AxiosError("Request rejected", "ERR_BAD_RESPONSE", config, undefined, {
    data: { error: { details: { reason } } },
    status,
    statusText: "Rejected",
    headers: {},
    config,
  });
}

const tokens = (access = "fresh-access") => ({
  access_token: access,
  token_type: "bearer",
  expires_in: 900,
});

async function world() {
  const locks = cookieLocks();
  Object.defineProperty(navigator, "locks", { configurable: true, value: locks });
  const http = await import("@/lib/api");
  const auth = await import("@/stores/auth");
  const bootstrap = await import("@/features/auth/bootstrap");
  const recovery = await import("@/features/auth/recovery");
  const cookie = await import("@/features/auth/cookieRequests");
  const logout = await import("@/features/auth/logoutState");
  const onAuthFailure = vi.fn(() => auth.useAuthStore.getState().clear());
  const requestMfaStepUp = vi.fn<() => Promise<string | null>>(async () => null);
  auth.useAuthStore.getState().setTokens(tokens("old-access"));
  const hooks = {
    getAccessToken: () => auth.useAuthStore.getState().accessToken,
    refreshTokens: () => bootstrap.refreshSessionFromCookie([0]),
    recoverWithFreshAccess: recovery.recoverSessionWithFreshAccess,
    requestMfaStepUp,
    onAuthFailure,
  };
  http.configureAuthHooks(hooks);
  return { http, auth, bootstrap, recovery, cookie, logout, locks, hooks, onAuthFailure };
}

const originalLocks = Object.getOwnPropertyDescriptor(navigator, "locks");

beforeEach(() => {
  vi.resetModules();
  window.localStorage.clear();
  window.sessionStorage.clear();
  for (const cookie of document.cookie.split(";")) {
    const name = cookie.split("=")[0]?.trim();
    if (name) document.cookie = `${name}=; Path=/; Max-Age=0`;
  }
});

afterEach(() => {
  if (originalLocks) Object.defineProperty(navigator, "locks", originalLocks);
  else Reflect.deleteProperty(navigator, "locks");
  vi.resetModules();
});

describe("protected recovery with the actual cookie coordinator", () => {
  it.each([401, 403])(
    "reports raw refresh %s as auth failure without replaying",
    async (status) => {
      const state = await world();
      const paths: string[] = [];
      state.http.api.defaults.adapter = async (config) => {
        paths.push(config.url ?? "");
        throw rejection(config, config.url === "/auth/refresh" ? status : 401);
      };
      await expect(state.http.api.get("/protected")).rejects.toMatchObject({
        response: { status: 401 },
      });
      expect(paths).toEqual(["/protected", "/auth/refresh"]);
      expect(state.onAuthFailure).toHaveBeenCalledTimes(1);
      expect(state.auth.useAuthStore.getState().accessToken).toBeNull();
    },
  );

  it.each([401, 403, 503])(
    "preserves a protected retry's %s without clearing auth or replaying again",
    async (status) => {
      const state = await world();
      const paths: string[] = [];
      let protectedCalls = 0;
      state.http.api.defaults.adapter = async (config) => {
        paths.push(config.url ?? "");
        if (config.url === "/auth/refresh") return response(config, tokens());
        protectedCalls += 1;
        throw rejection(config, protectedCalls === 1 ? 401 : status);
      };
      await expect(
        state.http.api.post("/protected", { operation_id: "original-operation" }),
      ).rejects.toMatchObject({ response: { status } });
      expect(paths).toEqual(["/protected", "/auth/refresh", "/protected"]);
      expect(state.onAuthFailure).not.toHaveBeenCalled();
      expect(state.auth.useAuthStore.getState().accessToken).toBe("fresh-access");
    },
  );

  it.each([
    [0, 30_000],
    [500, 500],
    [60_000, 30_000],
  ])(
    "bounds recovery after original timeout %i to %i and releases queued cookie work",
    async (originalTimeout, recoveryTimeout) => {
      const state = await world();
      const entered = deferred<void>();
      const observedTimeouts: Array<number | undefined> = [];
      let refreshCalls = 0;
      vi.useFakeTimers();
      try {
        state.http.api.defaults.adapter = async (config) => {
          if (config.url === "/auth/refresh") {
            refreshCalls += 1;
            return response(config, tokens());
          }
          observedTimeouts.push(config.timeout);
          if (observedTimeouts.length === 1) throw rejection(config, 401);
          entered.resolve();
          return new Promise<AxiosResponse<unknown>>((_resolve, reject) => {
            window.setTimeout(() => {
              reject(new AxiosError("Transport timed out", "ECONNABORTED", config));
            }, config.timeout);
          });
        };
        const denied = expect(
          state.http.api.get("/protected", { timeout: originalTimeout }),
        ).rejects.toMatchObject({ code: "ECONNABORTED" });
        await entered.promise;
        const nextCookieAction = vi.fn(async () => "next-cookie-operation");
        const following = state.cookie.runCookieRequest("account", nextCookieAction);
        await state.locks.queued(2);
        expect(observedTimeouts).toEqual([originalTimeout, recoveryTimeout]);
        expect(nextCookieAction).not.toHaveBeenCalled();
        await vi.advanceTimersByTimeAsync(recoveryTimeout - 1);
        expect(nextCookieAction).not.toHaveBeenCalled();
        await vi.advanceTimersByTimeAsync(1);
        const [, nextResult] = await Promise.all([denied, following]);
        expect(nextResult).toBe("next-cookie-operation");
        expect(nextCookieAction).toHaveBeenCalledTimes(1);
        expect(refreshCalls).toBe(1);
        expect(observedTimeouts).toHaveLength(2);
        expect(state.onAuthFailure).not.toHaveBeenCalled();
        expect(state.auth.useAuthStore.getState().accessToken).toBe("fresh-access");
      } finally {
        vi.useRealTimers();
      }
    },
  );

  it("does not restore an already logged-out epoch", async () => {
    const state = await world();
    state.logout.beginLogout(null);
    state.auth.useAuthStore.getState().clear();
    const adapter = vi.fn();
    const consume = vi.fn(async () => "must-not-run");
    state.http.api.defaults.adapter = adapter;
    await expect(
      state.recovery.recoverSessionWithFreshAccess(consume, () => undefined),
    ).resolves.toBeNull();
    expect(adapter).not.toHaveBeenCalled();
    expect(consume).not.toHaveBeenCalled();
    expect(state.onAuthFailure).not.toHaveBeenCalled();
  });

  it("cancels a queued recovery before rotating the cookie when its request is aborted", async () => {
    const state = await world();
    const entered = deferred<void>();
    const release = deferred<void>();
    const owner = state.cookie.runCookieRequest("account", async () => {
      entered.resolve();
      await release.promise;
    });
    await entered.promise;
    const paths: string[] = [];
    state.http.api.defaults.adapter = async (config) => {
      paths.push(config.url ?? "");
      throw rejection(config, 401);
    };
    const controller = new AbortController();
    const denied = expect(
      state.http.api.get("/protected", { signal: controller.signal }),
    ).rejects.toMatchObject({ code: "ERR_CANCELED" });
    await state.locks.queued(2);
    controller.abort();
    release.resolve();
    await Promise.all([owner, denied]);
    expect(paths).toEqual(["/protected"]);
    expect(state.onAuthFailure).not.toHaveBeenCalled();
  });

  it.each(["logout", "replacement login"])(
    "does not rotate or clear the next session after queued %s",
    async (change) => {
      const state = await world();
      const entered = deferred<void>();
      const release = deferred<void>();
      const owner = state.cookie.runCookieRequest("account", async () => {
        entered.resolve();
        await release.promise;
      });
      await entered.promise;
      const paths: string[] = [];
      state.http.api.defaults.adapter = async (config) => {
        paths.push(config.url ?? "");
        throw rejection(config, 401);
      };
      const denied = expect(state.http.api.get("/protected")).rejects.toMatchObject({
        code: "ERR_CANCELED",
      });
      await state.locks.queued(2);
      state.auth.useAuthStore.getState().clear();
      if (change === "logout") state.logout.beginLogout(null);
      else state.auth.useAuthStore.getState().setTokens(tokens("replacement-access"));
      release.resolve();
      await Promise.all([owner.catch(() => undefined), denied]);
      expect(paths).toEqual(["/protected"]);
      expect(state.auth.useAuthStore.getState().accessToken).toBe(
        change === "logout" ? null : "replacement-access",
      );
      expect(state.onAuthFailure).not.toHaveBeenCalled();
    },
  );

  it("rejects an old refresh response before publishing tokens into a replacement session", async () => {
    const state = await world();
    const started = deferred<void>();
    const release = deferred<void>();
    let protectedCalls = 0;
    state.http.api.defaults.adapter = async (config) => {
      if (config.url === "/auth/refresh") {
        started.resolve();
        await release.promise;
        return response(config, tokens());
      }
      protectedCalls += 1;
      throw rejection(config, 401);
    };
    const denied = expect(state.http.api.get("/protected")).rejects.toMatchObject({
      code: "ERR_CANCELED",
    });
    await started.promise;
    state.auth.useAuthStore.getState().clear();
    state.auth.useAuthStore.getState().setTokens(tokens("replacement-access"));
    release.resolve();
    await denied;
    expect(protectedCalls).toBe(1);
    expect(state.auth.useAuthStore.getState().accessToken).toBe("replacement-access");
    expect(state.onAuthFailure).not.toHaveBeenCalled();
  });

  it("finishes recovery queued behind a pending bootstrap without reacquiring its own lock", async () => {
    const state = await world();
    const started = deferred<void>();
    const release = deferred<void>();
    let refreshCalls = 0;
    let protectedCalls = 0;
    state.http.api.defaults.adapter = async (config) => {
      if (config.url === "/auth/refresh") {
        refreshCalls += 1;
        if (refreshCalls === 1) {
          started.resolve();
          await release.promise;
        }
        return response(config, tokens(`fresh-${refreshCalls}`));
      }
      protectedCalls += 1;
      if (protectedCalls === 1) throw rejection(config, 401);
      return response(config, { recovered: true });
    };
    const bootstrap = state.http.refreshAccessToken();
    await started.promise;
    const protectedRequest = state.http.api.get("/protected");
    await state.locks.queued(2);
    release.resolve();
    const [access, protectedResponse] = await Promise.all([bootstrap, protectedRequest]);
    expect(access).toBe("fresh-1");
    expect(protectedResponse.data).toEqual({ recovered: true });
    expect(refreshCalls).toBe(2);
    expect(protectedCalls).toBe(2);
    expect(state.onAuthFailure).not.toHaveBeenCalled();
  });

  it("releases the cookie lock before an MFA action acquires it", async () => {
    const state = await world();
    let protectedCalls = 0;
    const proofs: string[] = [];
    state.http.configureAuthHooks({
      ...state.hooks,
      requestMfaStepUp: () =>
        state.cookie.runCookieRequest("account", async () => {
          proofs.push("step-up");
          state.auth.useAuthStore.getState().setTokens(tokens("confirmed-access"));
          return "confirmed-access";
        }),
    });
    state.http.api.defaults.adapter = async (config) => {
      if (config.url === "/auth/refresh") return response(config, tokens());
      protectedCalls += 1;
      if (protectedCalls === 1) throw rejection(config, 401);
      if (protectedCalls === 2) throw rejection(config, 403, "mfa_step_up_required");
      expect(config.headers.get("Authorization")).toBe("Bearer confirmed-access");
      return response(config, { confirmed: true });
    };
    const result = await state.http.api.get("/protected");
    expect(result.data).toEqual({ confirmed: true });
    expect(proofs).toEqual(["step-up"]);
    expect(state.locks.events).toEqual([
      "queued:1:aurum:auth-cookie",
      "acquired:1",
      "released:1",
      "queued:2:aurum:auth-cookie",
      "acquired:2",
      "released:2",
    ]);
    expect(state.onAuthFailure).not.toHaveBeenCalled();
  });

  it("does not prompt for MFA after the recovered request is aborted with its 403", async () => {
    const state = await world();
    const controller = new AbortController();
    let protectedCalls = 0;
    state.http.api.defaults.adapter = async (config) => {
      if (config.url === "/auth/refresh") return response(config, tokens());
      protectedCalls += 1;
      if (protectedCalls === 1) throw rejection(config, 401);
      controller.abort();
      throw rejection(config, 403, "mfa_step_up_required");
    };
    await expect(
      state.http.api.get("/protected", { signal: controller.signal }),
    ).rejects.toMatchObject({ code: "ERR_CANCELED" });
    expect(protectedCalls).toBe(2);
    expect(state.hooks.requestMfaStepUp).not.toHaveBeenCalled();
    expect(state.onAuthFailure).not.toHaveBeenCalled();
  });

  it("does not replay after an abort while the MFA proof is pending", async () => {
    const state = await world();
    const started = deferred<void>();
    const proof = deferred<string>();
    const controller = new AbortController();
    let protectedCalls = 0;
    state.http.configureAuthHooks({
      ...state.hooks,
      requestMfaStepUp: async () => {
        started.resolve();
        return proof.promise;
      },
    });
    state.http.api.defaults.adapter = async (config) => {
      protectedCalls += 1;
      throw rejection(config, 403, "mfa_step_up_required");
    };
    const denied = expect(
      state.http.api.get("/protected", { signal: controller.signal }),
    ).rejects.toMatchObject({ code: "ERR_CANCELED" });
    await started.promise;
    controller.abort();
    proof.resolve("confirmed-access");
    await denied;
    expect(protectedCalls).toBe(1);
    expect(state.onAuthFailure).not.toHaveBeenCalled();
  });
});
