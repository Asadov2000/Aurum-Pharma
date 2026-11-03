import { AxiosError, type AxiosAdapter, type InternalAxiosRequestConfig } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

interface LockRequestObservation {
  name: string;
  blockedByActiveOwner: boolean;
}

/** Browser primitive only: independent module realms share this FIFO lock manager. */
function sharedWebLocks() {
  const tails = new Map<string, Promise<unknown>>();
  const owners = new Set<string>();
  const requests: LockRequestObservation[] = [];
  const observers = new Map<number, ReturnType<typeof deferred<LockRequestObservation>>>();
  const events: string[] = [];

  function request<T>(name: string, execute: () => Promise<T>): Promise<T> {
    const observation = { name, blockedByActiveOwner: owners.has(name) };
    const number = requests.push(observation);
    events.push(`queued:${number}:${name}`);
    observers.get(number)?.resolve(observation);
    const prior = tails.get(name) ?? Promise.resolve();
    const pending = prior.then(async () => {
      owners.add(name);
      events.push(`acquired:${number}:${name}`);
      try {
        return await execute();
      } finally {
        events.push(`released:${number}:${name}`);
        owners.delete(name);
      }
    });
    tails.set(
      name,
      pending.catch(() => undefined),
    );
    return pending;
  }

  function observeRequest(number: number): Promise<LockRequestObservation> {
    const existing = requests[number - 1];
    if (existing) return Promise.resolve(existing);
    const observation = deferred<LockRequestObservation>();
    observers.set(number, observation);
    return observation.promise;
  }

  return { request, observeRequest, events };
}

function rejected(config: InternalAxiosRequestConfig): AxiosError {
  return new AxiosError("Session no longer current", "ERR_BAD_RESPONSE", config, undefined, {
    data: { error: { code: "unauthorized" } },
    status: 401,
    statusText: "Unauthorized",
    headers: {},
    config,
  });
}

async function isolatedTab(name: string, adapter: AxiosAdapter, failures: string[]) {
  // Each import graph owns its real api singleton, cookie queue, auth store and
  // in-flight refresh promises, just as separate browser tabs do.
  vi.resetModules();
  const http = await import("@/lib/api");
  const { useAuthStore } = await import("@/stores/auth");
  const { refreshTokensRequest } = await import("@/features/auth/api");
  const { recoverSessionWithFreshAccess } = await import("@/features/auth/recovery");
  http.api.defaults.adapter = adapter;
  useAuthStore.getState().setTokens({
    access_token: "access-S1",
    token_type: "bearer",
    expires_in: 900,
  });
  http.configureAuthHooks({
    getAccessToken: () => useAuthStore.getState().accessToken,
    refreshTokens: async () => {
      const tokens = await refreshTokensRequest(crypto.randomUUID());
      useAuthStore.getState().setTokens(tokens);
      return tokens.access_token;
    },
    recoverWithFreshAccess: recoverSessionWithFreshAccess,
    requestMfaStepUp: async () => null,
    onAuthFailure: () => {
      failures.push(name);
    },
  });
  return { http, useAuthStore };
}

const originalLocks = Object.getOwnPropertyDescriptor(navigator, "locks");

beforeEach(() => {
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

describe("independent tabs sharing a rotating cookie session", () => {
  it("keeps the first protected retry valid until the second tab may rotate the cookie", async () => {
    const locks = sharedWebLocks();
    Object.defineProperty(navigator, "locks", { configurable: true, value: locks });
    const firstRetryEntered = deferred<void>();
    const releaseFirstRetry = deferred<void>();
    const secondRefreshCompleted = deferred<void>();
    const failures: string[] = [];
    const serverEvents: string[] = [];
    let currentSid = 2;
    const attempts = { A: 0, B: 0 };

    function serverAdapter(tab: "A" | "B"): AxiosAdapter {
      return async (config) => {
        if (config.url === "/auth/refresh") {
          currentSid += 1;
          serverEvents.push(`refresh:${tab}:S${currentSid}`);
          const response = {
            data: {
              access_token: `access-S${currentSid}`,
              token_type: "bearer",
              expires_in: 900,
            },
            status: 200,
            statusText: "OK",
            headers: {},
            config,
          };
          if (tab === "B") secondRefreshCompleted.resolve();
          return response;
        }
        if (config.url !== `/protected/${tab}`) throw new Error("Unexpected test request");
        attempts[tab] += 1;
        if (tab === "A" && attempts.A === 2) {
          firstRetryEntered.resolve();
          await releaseFirstRetry.promise;
        }
        if (config.headers.get("Authorization") !== `Bearer access-S${currentSid}`) {
          serverEvents.push(`unauthorized:${tab}`);
          throw rejected(config);
        }
        serverEvents.push(`protected:${tab}:S${currentSid}`);
        return {
          data: { tab },
          status: 200,
          statusText: "OK",
          headers: {},
          config,
        };
      };
    }

    const first = await isolatedTab("A", serverAdapter("A"), failures);
    const second = await isolatedTab("B", serverAdapter("B"), failures);
    expect(first.http.api).not.toBe(second.http.api);
    expect(first.useAuthStore).not.toBe(second.useAuthStore);

    const firstRequest = first.http.api.get<{ tab: string }>("/protected/A");
    await firstRetryEntered.promise;
    const secondRequest = second.http.api.get<{ tab: string }>("/protected/B");
    const outcomes = Promise.allSettled([firstRequest, secondRequest]);
    try {
      const queued = await locks.observeRequest(2);
      expect(queued.name).toBe("aurum:auth-cookie");
      // A legal adversarial schedule: if A already released the cookie lock,
      // let B rotate before the server validates A's suspended retry. If A is
      // still the owner, finish its retry so B can acquire the FIFO lock next.
      if (!queued.blockedByActiveOwner) await secondRefreshCompleted.promise;
    } finally {
      releaseFirstRetry.resolve();
    }
    const results = await outcomes;
    expect(results.map((result) => result.status)).toEqual(["fulfilled", "fulfilled"]);
    for (const result of results) {
      if (result.status === "fulfilled") expect(result.value.status).toBe(200);
    }
    expect(serverEvents).toEqual([
      "unauthorized:A",
      "refresh:A:S3",
      "unauthorized:B",
      "protected:A:S3",
      "refresh:B:S4",
      "protected:B:S4",
    ]);
    expect(locks.events).toEqual([
      "queued:1:aurum:auth-cookie",
      "acquired:1:aurum:auth-cookie",
      "queued:2:aurum:auth-cookie",
      "released:1:aurum:auth-cookie",
      "acquired:2:aurum:auth-cookie",
      "released:2:aurum:auth-cookie",
    ]);
    expect(attempts).toEqual({ A: 2, B: 2 });
    expect(failures).toEqual([]);
  });
});
