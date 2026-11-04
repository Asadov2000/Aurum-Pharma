import { CanceledError } from "axios";

import { getAuthSessionGeneration, useAuthStore } from "@/stores/auth";
import { api, withoutAuth } from "@/lib/api";

import { AUTH_REQUEST_TIMEOUT_MS } from "./api";
import { runCookieRequest } from "./cookieRequests";
import { isConfirmedAuthFailure, isTransientRefreshFailure } from "./failures";
import { getAuthEpoch, isSessionRestorationBlocked } from "./logoutState";
import { getOrCreateRefreshOperationId } from "./refreshOperation";
import { rememberTokenEpoch } from "./tokenEpoch";
import type { TokenPair } from "./types";

const RETRY_DELAYS_MS = [0, 300, 900] as const;

/** Keep a rotating cookie and one protected request in the same origin lock. */
export async function recoverSessionWithFreshAccess<T>(
  consume: (accessToken: string) => Promise<T>,
  assertRequestCurrent: () => void,
): Promise<T | null> {
  if (isSessionRestorationBlocked()) return null;
  const generation = getAuthSessionGeneration();
  const epoch = getAuthEpoch();
  const ensureCurrentSession = () => {
    if (
      generation !== getAuthSessionGeneration() ||
      epoch !== getAuthEpoch() ||
      isSessionRestorationBlocked()
    ) {
      throw new CanceledError("Authentication session changed");
    }
    assertRequestCurrent();
  };
  const operationId = getOrCreateRefreshOperationId();
  let lastError: unknown;
  for (const [index, delayMs] of RETRY_DELAYS_MS.entries()) {
    if (delayMs > 0) await new Promise<void>((resolve) => window.setTimeout(resolve, delayMs));
    ensureCurrentSession();
    let consumeStarted = false;
    try {
      return await runCookieRequest("refresh", async () => {
        ensureCurrentSession();
        const { data: tokens } = await api.post<TokenPair>(
          "/auth/refresh",
          { operation_id: operationId },
          withoutAuth({ timeout: AUTH_REQUEST_TIMEOUT_MS }),
        );
        consumeStarted = true;
        ensureCurrentSession();
        // The outer cookie wrapper will see the consumed response, not this
        // TokenPair. Bind the tokens to the checked epoch before installing them.
        useAuthStore.getState().setTokens(rememberTokenEpoch(tokens, epoch));
        ensureCurrentSession();
        return await consume(tokens.access_token);
      });
    } catch (error) {
      ensureCurrentSession();
      // An error from the original operation is not a failed refresh. In
      // particular, a timeout/503 must never cause another financial command.
      if (consumeStarted) throw error;
      if (isConfirmedAuthFailure(error)) return null;
      lastError = error;
      if (index === RETRY_DELAYS_MS.length - 1 || !isTransientRefreshFailure(error)) throw error;
    }
  }
  throw lastError;
}
