// Wires the axios instance up to the auth store. Called once from main.tsx
// before the router renders, so any in-flight 401 can ask the store for a
// fresh refresh token instead of going through an empty closure.

import { configureAuthHooks, refreshAccessToken } from "@/lib/api";
import { useAuthStore } from "@/stores/auth";

import { refreshTokensRequest } from "./api";
import { isConfirmedAuthFailure, isTransientRefreshFailure } from "./failures";
import { getOrCreateRefreshOperationId } from "./refreshOperation";
import { requestMfaStepUp } from "./stepUpCoordinator";

const DEFAULT_RETRY_DELAYS_MS = [0, 300, 900] as const;
const BOOTSTRAP_RETRY_MAX_MS = 30_000;

function wait(delayMs: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, delayMs));
}

export async function refreshSessionFromCookie(
  retryDelaysMs: readonly number[] = DEFAULT_RETRY_DELAYS_MS,
): Promise<string | null> {
  const operationId = getOrCreateRefreshOperationId();
  let lastError: unknown;

  for (const [index, delayMs] of retryDelaysMs.entries()) {
    if (delayMs > 0) await wait(delayMs);
    try {
      const tokens = await refreshTokensRequest(operationId);
      useAuthStore.getState().setTokens(tokens);
      return tokens.access_token;
    } catch (error) {
      if (isConfirmedAuthFailure(error)) return null;
      lastError = error;
      const hasAnotherAttempt = index < retryDelaysMs.length - 1;
      if (!hasAnotherAttempt || !isTransientRefreshFailure(error)) throw error;
    }
  }

  throw lastError;
}

let retryTimer: number | null = null;
let retryDelayMs = 3_000;
let onlineListenerInstalled = false;

function runBootstrapRefresh(): void {
  if (useAuthStore.getState().hydrated) return;
  if (retryTimer !== null) {
    window.clearTimeout(retryTimer);
    retryTimer = null;
  }
  void refreshAccessToken()
    .then(() => {
      retryDelayMs = 3_000;
    })
    .catch(() => {
      if (retryTimer !== null) return;
      retryTimer = window.setTimeout(() => {
        retryTimer = null;
        retryDelayMs = Math.min(retryDelayMs * 2, BOOTSTRAP_RETRY_MAX_MS);
        if (!useAuthStore.getState().hydrated) runBootstrapRefresh();
      }, retryDelayMs);
    });
}

export function bootstrapAuth(): void {
  configureAuthHooks({
    getAccessToken: () => useAuthStore.getState().accessToken,
    refreshTokens: refreshSessionFromCookie,
    requestMfaStepUp,
    onAuthFailure: () => {
      useAuthStore.getState().clear();
    },
  });
  if (!onlineListenerInstalled) {
    window.addEventListener("online", runBootstrapRefresh);
    onlineListenerInstalled = true;
  }
  runBootstrapRefresh();
}
